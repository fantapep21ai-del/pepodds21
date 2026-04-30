"""
Celery tasks — thin wrappers around service layer.

All async work is run via asyncio.run() since Celery workers are sync by default.
Each task logs start/end and writes a PipelineRun record for auditing.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run(coro):
    """Run a coroutine from a sync Celery task."""
    return asyncio.run(coro)


# ── Auto-discovery competizioni ───────────────────────────────────────────────

@celery_app.task(name="app.workers.tasks.sync_competitions")
def sync_competitions():
    """
    Ogni mattina alle 09:00 UTC — chiama The Odds API, trova nuovi tornei
    (tennis ATP/WTA, basket, calcio) e li inserisce automaticamente nel DB.
    Non tocca le competizioni già esistenti.
    """
    result = _run(_sync_competitions_async())
    logger.info("sync_competitions done: %s", result)
    return result


async def _sync_competitions_async() -> dict:
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from app.db.base import AsyncSessionLocal
    from app.db.models.match import Competition
    from app.services.odds_fetcher import OddsAPIClient
    from app.services.ingestion_service import SPORT_KEY_MAPPING

    client = OddsAPIClient()
    try:
        sports = await client.list_sports()
    except Exception as exc:
        logger.error("sync_competitions: list_sports failed: %s", exc)
        return {"added": 0, "error": str(exc)}

    # Costruisci set di sport_key autorizzati da SPORT_KEY_MAPPING
    authorized_keys = set()
    for sport, mapping in SPORT_KEY_MAPPING.items():
        authorized_keys.update(mapping.values())

    added = 0
    async with AsyncSessionLocal() as db:
        # Carica le chiavi già presenti
        result = await db.execute(select(Competition.odds_api_key))
        existing_keys = {row[0] for row in result.all()}

        for s in sports:
            key = s.get("key", "")
            title = s.get("title", key)

            # Salta sport non autorizzati
            if key not in authorized_keys:
                continue

            # Classifica sport
            if "tennis" in key:
                sport = "tennis"
            elif "soccer" in key or "football" in key:
                sport = "football"
            elif "basketball" in key or "nba" in key or "nbl" in key:
                sport = "basketball"
            else:
                continue  # sport non supportato

            if key in existing_keys:
                continue  # già in DB

            # Determina tier e peso automaticamente
            key_lower = key.lower()
            title_lower = title.lower()
            if any(x in key_lower for x in ["wimbledon", "us_open", "french_open", "aus_open", "australian"]):
                tier, weight = "S", 1.0
                name = title
            elif any(x in key_lower for x in ["masters", "1000", "madrid", "rome", "miami",
                                                "canadian", "montreal", "cincinnati", "shanghai",
                                                "paris_masters", "indian_wells"]):
                tier, weight = "A", 0.9
                name = title
            elif "atp" in key_lower and "500" in title_lower:
                tier, weight = "A", 0.8
                name = title
            elif "atp" in key_lower:
                tier, weight = "A", 0.8
                name = title
            elif "wta" in key_lower:
                tier, weight = "B", 0.6
                name = title
            elif "nba" in key_lower:
                tier, weight = "A", 0.9
                name = title
            elif "serie_a" in key_lower or "epl" in key_lower or "champs" in key_lower:
                tier, weight = "A", 0.9
                name = title
            else:
                tier, weight = "B", 0.6
                name = title

            # Use simple INSERT instead of pg_insert to avoid constraint issues
            try:
                await db.execute(
                    "INSERT INTO competitions (id, name, sport, tier, weight, odds_api_key) VALUES (gen_random_uuid(), %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    [name, sport, tier, weight, key]
                )
                existing_keys.add(key)
                added += 1
                logger.info("Nuova competizione aggiunta: %s (%s)", name, key)
            except Exception as e:
                logger.warning("Skipped competition %s: %s", key, e)

        if added:
            await db.commit()

    return {"added": added}


# ── Odds ──────────────────────────────────────────────────────────────────────

async def fetch_all_odds_async(sport: str | None = None) -> dict:
    """
    Fetch odds per un singolo sport o per tutti gli sport.
    Chiamabile on-demand da Telegram handler.

    Args:
        sport: "football", "basketball", "tennis", o None per tutti.
    """
    from app.db.base import AsyncSessionLocal
    from app.services.ingestion_service import IngestionService

    async with AsyncSessionLocal() as db:
        svc = IngestionService(db)
        return await svc.ingest_all_odds(sport=sport)


@celery_app.task(name="app.workers.tasks.fetch_all_odds_task", bind=True, max_retries=3)
def fetch_all_odds_task(self, sport: str | None = None):
    """Celery task wrapper per fetch_all_odds_async. Chiamato da Telegram."""
    logger.info("Task fetch_all_odds_task started (sport=%s)", sport or "all")
    try:
        counts = _run(fetch_all_odds_async(sport))
        logger.info("fetch_all_odds_task done: %s", counts)
        return counts
    except Exception as exc:
        logger.error("fetch_all_odds_task failed: %s", exc)
        raise self.retry(exc=exc, countdown=60)


# ── Upcoming stats (forma + infortuni per le prossime 48h) ───────────────────

async def fetch_upcoming_stats_async(sport: str | None = None, hours_lookahead: int = 48) -> dict:
    """
    Arricchisce il contesto delle partite con:
    - Forma recente (standings API-Football: ultimi 5 risultati)
    - Infortuni/squalifiche (ESPN per NBA, api-sports per calcio)
    - H2H per partite dove è stato trovato valore
    - Ranking tennis (ATP/WTA)

    Args:
        sport: "football", "basketball", "tennis", o None per tutti
        hours_lookahead: finestra temporale (default 48h, 20h per ricerche sport-specific)

    Callable on-demand da Telegram handler.
    """
    return await _fetch_upcoming_stats_async(sport, hours_lookahead=hours_lookahead)


async def _fetch_upcoming_stats_async(sport: str | None = None, hours_lookahead: int = 48) -> dict:
    from datetime import timedelta
    from sqlalchemy import select, and_
    from app.db.base import AsyncSessionLocal
    from app.db.models.match import Match, Competition
    from app.services.stats_fetcher import (
        FootballStatsClient, FootballDataClient, ClubEloClient,
        TennisStatsClient, NBAInjuryClient, WeatherClient,
        LEAGUE_MAP, FOOTBALL_DATA_MAP,
    )
    from app.config import settings

    football_client = FootballStatsClient() if settings.api_football_key else None
    fd_client       = FootballDataClient()   if getattr(settings, "football_data_key", "") else None
    tennis_client   = TennisStatsClient()    if settings.api_football_key else None
    nba_injury_client = NBAInjuryClient()     # ESPN, sempre attivo (no key)
    weather_client    = WeatherClient()        # Open-Meteo, sempre attivo (no key)
    clubelo_client    = ClubEloClient()        # gratuito, sempre attivo

    # Dunkest (statistiche giocatori NBA) + NBA news aggregator — gratuiti
    from app.services.dunkest_client import DunkestClient, find_player_id, MARKET_TO_STAT
    from app.services.nba_news_client import NBAInjuryAggregator
    dunkest_client = DunkestClient()
    nba_news_aggregator = NBAInjuryAggregator()

    if not football_client and not fd_client:
        logger.info("Nessuna API stats premium — uso solo ClubElo + meteo come fallback")

    updated = 0
    errors  = 0
    now    = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=hours_lookahead)

    # ── Pre-fetch NBA injuries (una sola chiamata ESPN per tutte le squadre) ──
    nba_injuries: dict[str, list[dict]] = {}
    try:
        nba_injuries = await nba_injury_client.get_injuries()
        logger.info("ESPN NBA injuries: %d squadre con infortuni", len(nba_injuries))
    except Exception as exc:
        logger.warning("ESPN NBA injuries fetch failed: %s", exc)

    # ── Pre-fetch rankings tennis (una sola chiamata per ATP e WTA) ──────────
    atp_rankings: list[dict] = []
    wta_rankings: list[dict] = []
    if tennis_client:
        try:
            atp_rankings = await tennis_client.get_rankings("atp")
        except Exception as exc:
            logger.warning("Tennis ATP rankings failed: %s", exc)
        try:
            wta_rankings = await tennis_client.get_rankings("wta")
        except Exception as exc:
            logger.warning("Tennis WTA rankings failed: %s", exc)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Match)
            .join(Competition, Competition.id == Match.competition_id)
            .where(
                and_(
                    Match.status == "scheduled",
                    Match.match_date >= now,
                    Match.match_date <= cutoff,
                    # Tutti gli sport — rimuoviamo il filtro "football" only
                )
            )
            .limit(40)
        )
        matches = result.scalars().all()

        if not matches:
            return {"updated": 0, "no_matches": True}

        # ── Fetch fixtures per data (api-sports.io) ───────────────────────────
        fixtures_by_date: dict = {}
        if football_client:
            dates_needed = {m.match_date.date() for m in matches}
            for d in dates_needed:
                try:
                    fixtures_by_date[d] = await football_client.get_fixtures_by_date(d)
                except Exception as exc:
                    logger.warning("Fixtures by date %s failed: %s", d, exc)
                    fixtures_by_date[d] = []

        # ── Standings cache (prova football-data.org prima, poi api-sports.io) ─
        standings_cache: dict[str, dict] = {}

        for match in matches:
            match_sport = (match.sport or "").lower()

            # Filtra per sport se parametro è fornito
            if sport and match_sport != sport:
                continue

            comp_result = await db.execute(
                select(Competition).where(Competition.id == match.competition_id)
            )
            comp = comp_result.scalar_one_or_none()
            if not comp:
                continue

            raw_stats = match.raw_stats or {}

            # ════════════════════════════════════════════════════════════════
            # CALCIO
            # ════════════════════════════════════════════════════════════════
            if match_sport == "football":
                odds_key = comp.odds_api_key or ""
                cache_key = odds_key

                if cache_key not in standings_cache:
                    standings: dict = {}
                    if fd_client and odds_key in FOOTBALL_DATA_MAP:
                        fd_code = FOOTBALL_DATA_MAP[odds_key]
                        try:
                            raw_fd = await fd_client.get_standings(fd_code)
                            standings = fd_client.parse_standings(raw_fd)
                        except Exception as exc:
                            logger.warning("football-data.org %s: %s", fd_code, exc)
                    if not standings and football_client and odds_key in LEAGUE_MAP:
                        league_id, season = LEAGUE_MAP[odds_key]
                        try:
                            raw = await football_client.get_standings(league_id, season)
                            standings = football_client.parse_standings_form(raw)
                        except Exception as exc:
                            logger.warning("api-sports.io standings %s: %s", league_id, exc)
                    standings_cache[cache_key] = standings

                standings = standings_cache.get(cache_key, {})

                # Infortuni
                injuries = []
                if football_client:
                    day_fixtures = fixtures_by_date.get(match.match_date.date(), [])
                    fixture = football_client.find_fixture(
                        day_fixtures, match.home_team or "", match.away_team or ""
                    )
                    if fixture:
                        fixture_id = fixture.get("fixture", {}).get("id")
                        if fixture_id:
                            try:
                                raw_inj = await football_client.get_injuries(fixture_id)
                                injuries = football_client.parse_injuries(raw_inj)
                            except Exception as exc:
                                logger.warning("Injuries %s: %s", fixture_id, exc)

                # Standings match con fallback parziale
                home_key = (match.home_team or "").lower().strip()
                away_key = (match.away_team or "").lower().strip()
                home_st = standings.get(home_key, {})
                away_st = standings.get(away_key, {})
                if not home_st:
                    for k, v in standings.items():
                        if _partial_match(k, home_key):
                            home_st = v; break
                if not away_st:
                    for k, v in standings.items():
                        if _partial_match(k, away_key):
                            away_st = v; break

                # ClubElo fallback
                elo_proxy: dict = {}
                if not home_st or not away_st:
                    try:
                        match_day = match.match_date.date() if match.match_date else now.date()
                        elo_map = await clubelo_client.get_elo_for_date(match_day)
                        home_elo = clubelo_client.find_team_elo(elo_map, match.home_team or "")
                        away_elo = clubelo_client.find_team_elo(elo_map, match.away_team or "")
                        if home_elo or away_elo:
                            elo_proxy = clubelo_client.elo_to_form_proxy(home_elo, away_elo)
                    except Exception as exc:
                        logger.warning("ClubElo %s: %s", match.display_name(), exc)

                # Meteo (mercati totals — impatto su Over/Under gol)
                weather: dict = {}
                try:
                    weather = await weather_client.get_weather_for_match(
                        match.home_team or "", match.match_date
                    )
                except Exception as exc:
                    logger.debug("Weather %s: %s", match.display_name(), exc)

                # Notizie calcio (RSS feeds — gratis, no API key)
                football_news: str = ""
                try:
                    from app.services.football_news_service import FootballNewsService
                    news_svc = FootballNewsService()
                    football_news = await news_svc.get_match_news(
                        match.home_team or "", match.away_team or ""
                    )
                except Exception as exc:
                    logger.debug("FootballNews %s: %s", match.display_name(), exc)

                raw_stats.update({
                    "form": {"home": home_st, "away": away_st},
                    "injuries": injuries,
                    "standings_updated_at": now.isoformat(),
                })
                if elo_proxy:
                    raw_stats["elo"] = elo_proxy
                if weather:
                    raw_stats["weather"] = weather
                    if weather.get("totals_impact") in ("under_bias", "slight_under"):
                        logger.info(
                            "Meteo %s: %s (%s) — segnale under su totals",
                            match.display_name(),
                            weather.get("conditions", ""),
                            weather.get("totals_impact", ""),
                        )
                if football_news:
                    raw_stats["news"] = football_news

            # ════════════════════════════════════════════════════════════════
            # TENNIS
            # ════════════════════════════════════════════════════════════════
            elif match_sport == "tennis":
                if not tennis_client or (not atp_rankings and not wta_rankings):
                    continue

                player_a = match.player_a or match.home_team or ""
                player_b = match.player_b or match.away_team or ""
                comp_name = (comp.name or "").lower()

                rankings = wta_rankings if "wta" in comp_name else atp_rankings
                if not rankings:
                    continue

                try:
                    elo_context = tennis_client.build_player_elo_context(
                        rankings, player_a, player_b
                    )
                    if elo_context:
                        raw_stats["elo"] = elo_context
                        logger.info(
                            "Tennis ELO per %s: %s (rank %s) vs %s (rank %s)",
                            match.display_name(),
                            player_a,
                            elo_context.get("player_a_rank", "?"),
                            player_b,
                            elo_context.get("player_b_rank", "?"),
                        )
                except Exception as exc:
                    logger.warning("Tennis ELO %s: %s", match.display_name(), exc)

            # ════════════════════════════════════════════════════════════════
            # BASKET / NBA
            # ════════════════════════════════════════════════════════════════
            elif match_sport in ("basketball", "basket"):
                import asyncio as _asyncio
                home_key = (match.home_team or "").lower().strip()
                away_key = (match.away_team or "").lower().strip()

                # ── Infortuni ESPN ──────────────────────────────────────────
                home_inj: list[dict] = []
                away_inj: list[dict] = []
                for team_key_lower, inj_list in nba_injuries.items():
                    if _partial_match(team_key_lower, home_key):
                        home_inj = inj_list
                    elif _partial_match(team_key_lower, away_key):
                        away_inj = inj_list

                from app.services.stats_fetcher import NBAInjuryClient as _NIClient
                home_impact = _NIClient.assess_impact(home_inj)
                away_impact = _NIClient.assess_impact(away_inj)

                # ── NBA News (infortuni last-minute, rientri, rotazioni) ────
                news_summary: str = ""
                try:
                    all_news = await nba_news_aggregator.fetch_all()
                    confirmed_out = nba_news_aggregator.filter_confirmed_out(all_news)
                    # Filtra notizie rilevanti per questa partita
                    home_words = set((match.home_team or "").lower().split())
                    away_words = set((match.away_team or "").lower().split())
                    relevant = [
                        n for n in confirmed_out
                        if any(w in (n.get("player_name") or "").lower() for w in home_words | away_words)
                        or any(w in (n.get("team") or "").lower() for w in home_words | away_words)
                    ]
                    if relevant:
                        lines = [
                            f"{n['player_name']} ({n.get('team','')}) — {n['status'].upper()}: {n.get('reason','')}"
                            for n in relevant[:5]
                        ]
                        news_summary = " | ".join(lines)
                except Exception as exc:
                    logger.debug("NBA news fetch failed for %s: %s", match.display_name(), exc)

                # ── Dunkest: stats giocatori chiave ────────────────────────
                dunkest_data: dict = {}
                try:
                    # Giocatori chiave OUT (impatto usage)
                    out_players = [
                        n for n in (home_inj + away_inj)
                        if n.get("status") in ("out", "doubtful")
                    ]
                    # Fetch stats dei titolari della squadra con assenti
                    key_players: list[str] = []
                    if home_impact in ("high", "medium"):
                        # Cerca i titolari della squadra home (noti per usage alto)
                        key_players += [p.get("player_name", "") for p in home_inj[:3]]
                    if away_impact in ("high", "medium"):
                        key_players += [p.get("player_name", "") for p in away_inj[:3]]

                    # Fetch top 3 giocatori con Dunkest (parallelo, max 3 calls)
                    fetch_tasks = []
                    fetched_names = []
                    for name in key_players[:3]:
                        pid = find_player_id(name)
                        if pid:
                            fetch_tasks.append(dunkest_client.get_player_games(pid))
                            fetched_names.append((name, pid))

                    if fetch_tasks:
                        results = await _asyncio.gather(*fetch_tasks, return_exceptions=True)
                        for (name, pid), games in zip(fetched_names, results):
                            if isinstance(games, Exception) or not games:
                                continue
                            recent_avg = dunkest_client.get_recent_averages(games, last_n=5)
                            b2b = dunkest_client.assess_back_to_back(
                                games,
                                match.match_date.strftime("%Y-%m-%d") if match.match_date else ""
                            )
                            dunkest_data[name] = {
                                "recent_avg_5": recent_avg,
                                "back_to_back": b2b,
                                "games_sample": len([g for g in games if (g.get("min") or 0) >= 10]),
                            }
                            logger.debug("Dunkest %s: pts_avg5=%.1f b2b=%s",
                                        name, recent_avg.get("pts", 0), b2b)
                except Exception as exc:
                    logger.warning("Dunkest fetch failed for %s: %s", match.display_name(), exc)

                if home_impact == "high" or away_impact == "high":
                    logger.info(
                        "NBA injuries %s: home=%s away=%s | news=%s",
                        match.display_name(), home_impact, away_impact,
                        "sì" if news_summary else "no",
                    )

                raw_stats.update({
                    "injuries": {
                        "home": home_inj,
                        "away": away_inj,
                        "home_impact": home_impact,
                        "away_impact": away_impact,
                    },
                    "injuries_updated_at": now.isoformat(),
                    "news": news_summary or None,
                    "dunkest": dunkest_data or None,
                })

            match.raw_stats = raw_stats
            updated += 1

        await db.commit()

    return {"updated": updated, "errors": errors}


def _partial_match(a: str, b: str) -> bool:
    words_a = {w for w in a.split() if len(w) > 3}
    words_b = {w for w in b.split() if len(w) > 3}
    return bool(words_a & words_b)


# ── NBA Injury Monitoring ─────────────────────────────────────────────────────

@celery_app.task(name="app.workers.tasks.check_nba_injuries", bind=True, max_retries=1)
def check_nba_injuries(self):
    """
    1x al giorno alle 17:00 UTC: fetch injury NBA (ESPN, Rotowire),
    confronta con stato precedente in Redis, e se rileva nuovi OUT lancia
    l'absence pipeline per analizzare l'impatto su compagni e quote.
    """
    logger.info("Task check_nba_injuries started")
    try:
        result = _run(_check_nba_injuries_async())
        logger.info("check_nba_injuries done: %s", result)
        return result
    except Exception as exc:
        logger.error("check_nba_injuries failed: %s", exc)
        raise self.retry(exc=exc, countdown=60)


async def _check_nba_injuries_async() -> dict:
    import json
    from app.db.base import AsyncSessionLocal
    from app.services.nba_news_client import NBAInjuryAggregator
    from app.agents.nba_absence_pipeline import run_absence_analysis

    aggregator = NBAInjuryAggregator()
    all_injuries = await aggregator.fetch_all()
    confirmed_out = aggregator.filter_confirmed_out(all_injuries)

    logger.info(
        "NBA injuries: %d totali, %d confermati OUT/Doubtful",
        len(all_injuries), len(confirmed_out),
    )

    # ── Confronta con stato precedente in Redis ───────────────────────────────
    new_outs: list[dict] = []
    try:
        import redis as _redis
        from app.config import settings
        r = _redis.from_url(settings.redis_url_with_auth, decode_responses=True)

        for injury in confirmed_out:
            player_name = injury["player_name"]
            team        = injury["team"]
            status      = injury["status"]
            slug        = player_name.lower().replace(" ", "-")
            redis_key   = f"nba:injury:{slug}"

            prev_status = r.get(redis_key)
            if prev_status != status:
                # Nuovo stato o primo rilevamento
                if prev_status not in ("out", "doubtful") and status in ("out", "doubtful"):
                    # Nuova assenza confermata!
                    new_outs.append(injury)
                    logger.info(
                        "NUOVA ASSENZA: %s (%s) → %s (era: %s)",
                        player_name, team, status, prev_status or "unknown",
                    )

                # Aggiorna Redis (TTL 12 ore — si resetta ad ogni giornata)
                r.setex(redis_key, 43200, status)

    except Exception as exc:
        logger.warning("Redis injury check failed: %s", exc)
        # Fallback: considera tutti i confirmed_out come potenziali nuovi
        new_outs = confirmed_out[:5]  # limita per sicurezza

    # ── Lancia absence pipeline per ogni nuovo OUT ────────────────────────────
    results: list[dict] = []
    async with AsyncSessionLocal() as db:
        for injury in new_outs:
            player_name = injury["player_name"]
            team        = injury["team"] or ""
            try:
                result = await run_absence_analysis(
                    absent_player_name=player_name,
                    absent_team=team,
                    db=db,
                )
                results.append(result)
            except Exception as exc:
                logger.error(
                    "Absence pipeline failed for %s: %s", player_name, exc
                )

    return {
        "total_injuries":  len(all_injuries),
        "confirmed_out":   len(confirmed_out),
        "new_absences":    len(new_outs),
        "pipelines_run":   len(results),
        "results":         results,
    }


# ── Stats ─────────────────────────────────────────────────────────────────────

@celery_app.task(name="app.workers.tasks.fetch_all_stats", bind=True, max_retries=2)
def fetch_all_stats(self):
    """Fetch stats for matches that recently finished (status='finished', raw_stats=null)."""
    logger.info("Task fetch_all_stats started")
    try:
        count = _run(_fetch_all_stats_async())
        logger.info("fetch_all_stats done: %d matches updated", count)
        return {"updated": count}
    except Exception as exc:
        logger.error("fetch_all_stats failed: %s", exc)
        raise self.retry(exc=exc, countdown=120)


async def _fetch_all_stats_async() -> int:
    from sqlalchemy import select
    from app.db.base import AsyncSessionLocal
    from app.db.models.match import Match
    from app.services.ingestion_service import IngestionService

    async with AsyncSessionLocal() as db:
        svc = IngestionService(db)
        # Fetch stats per tutte le partite finite senza raw_stats (tutti gli sport)
        result = await db.execute(
            select(Match)
            .where(Match.status == "finished")
            .where(Match.raw_stats.is_(None))
            .limit(20)
        )
        matches = result.scalars().all()
        count = 0
        for match in matches:
            sport = (match.sport or "").lower()
            if sport == "football":
                ok = await svc.ingest_football_stats(match)
            else:
                # Basketball e tennis: nessuna stats post-match da API esterne,
                # ma segna raw_stats come {} per evitare re-processing continuo.
                match.raw_stats = match.raw_stats or {}
                ok = True
            if ok:
                count += 1
        if count:
            await db.commit()
        return count


# ── Full pipeline ─────────────────────────────────────────────────────────────

@celery_app.task(name="app.workers.tasks.run_daily_pipeline", bind=True, max_retries=1)
def run_daily_pipeline(self):
    """
    Full analysis pipeline — analizza i match arricchiti e genera opportunità.

    Pipeline:
    1. Seleziona match nelle prossime 48h (filtrato per sport se specificato)
    2. Per ogni match → run agenti AI in parallelo
    3. Consensus engine → identifica opportunità
    4. Risk engine → dimensiona scommesse
    5. Telegram notification
    """
    import redis
    from app.config import settings

    # Leggi il sport e command_timestamp da Redis (salvati in telegram_webhook.py)
    task_id = self.request.id
    sport = None
    command_timestamp = None
    try:
        r = redis.Redis.from_url(settings.redis_url_with_auth, decode_responses=True)
        sport_value = r.get(f"celery:sport:{task_id}")
        sport = sport_value if (sport_value and sport_value != "all") else None

        timestamp_str = r.get(f"celery:timestamp:{task_id}")
        if timestamp_str:
            from datetime import datetime
            command_timestamp = datetime.fromisoformat(timestamp_str)

        logger.info("🔥 run_daily_pipeline ENTRY: task_id=%s, sport=%r, command_ts=%s (from Redis)", task_id, sport, command_timestamp)
    except Exception as exc:
        logger.warning("Failed to read params from Redis: %s", exc)
        sport = None
        command_timestamp = None

    logger.info("Task run_daily_pipeline started (sport=%s)", sport or "all")
    try:
        result = _run(_run_daily_pipeline_async(sport=sport, command_timestamp=command_timestamp))
        logger.info("run_daily_pipeline done: %s", result)
        return result
    except Exception as exc:
        logger.error("run_daily_pipeline failed (sport=%s): %s", sport, exc)
        raise self.retry(exc=exc, countdown=300)


async def _run_daily_pipeline_async(sport: str | None = None, command_timestamp: datetime | None = None) -> dict:
    from datetime import timedelta
    from sqlalchemy import select, and_
    from app.db.base import AsyncSessionLocal
    from app.db.models.match import Match, MatchOdds
    from app.db.models.opportunity import BettingOpportunity
    from app.db.models.runs import PipelineRun
    from app.services.ingestion_service import IngestionService

    logger.info("🔥 _run_daily_pipeline_async ENTRY: sport=%r, command_timestamp=%s", sport, command_timestamp)

    started_at = datetime.now(timezone.utc)
    pipeline_run_id = None

    async with AsyncSessionLocal() as db:
        # Log pipeline start
        pipeline = PipelineRun(started_at=started_at, status="running")
        db.add(pipeline)
        await db.commit()
        await db.refresh(pipeline)
        pipeline_run_id = pipeline.id

        try:
            # Step 1: usa quote già in DB (fetch separato via fetch_all_odds alle 09:30 UTC)
            # NON ri-fetcha le quote qui — evita sprechi quota API (500 req/mese free plan)

            # Step 2: find matches in next 18h with fresh odds (filtrato per sport + league se specificato)
            from sqlalchemy.orm import selectinload
            from sqlalchemy import join

            # Use command timestamp for 18h window, or fallback to NOW() if not provided
            if command_timestamp:
                cutoff = command_timestamp + timedelta(hours=18)
                logger.info("⏱️ Using command timestamp for 18h window: %s to %s", command_timestamp, cutoff)
            else:
                cutoff = datetime.now(timezone.utc) + timedelta(hours=18)
                logger.info("⏱️ Using NOW() for 18h window (fallback): %s", cutoff)

            where_conditions = [
                Match.status == "scheduled",
                Match.match_date <= cutoff,
            ]

            # Filtro sport opzionale
            if sport:
                where_conditions.append(Match.sport == sport)

            result = await db.execute(
                select(Match)
                .options(selectinload(Match.competition))
                .where(and_(*where_conditions))
            )
            matches_raw = result.scalars().all()

            # Filtra per allowed leagues se sport-specific
            if sport:
                from app.services.ingestion_service import normalize_league_name, is_league_allowed
                matches = []
                for m in matches_raw:
                    if m.competition:
                        canonical = normalize_league_name(m.competition.name, sport)
                        if canonical and is_league_allowed(canonical, sport):
                            matches.append(m)
                logger.info(
                    "🏛️ League filtering (sport=%s): %d raw → %d allowed",
                    sport, len(matches_raw), len(matches)
                )
            else:
                matches = matches_raw

            # Step 3: dispatch agent analysis for each match
            from app.agents.pipeline import analyse_match
            import redis as _redis
            opportunities_found = 0
            matches_report = []  # Collect all matches with their singole

            # Get Redis client for CLV blacklist
            redis_client = _redis.from_url(settings.redis_url_with_auth, decode_responses=True)

            for match in matches:
                n = await analyse_match(match, db, redis_client=redis_client)
                opportunities_found += n

                # Reload match to get updated analysis_status and analysis_reason
                await db.refresh(match)

                # Find ALL qualifying singole for this match (not just best one)
                opps_result = await db.execute(
                    select(BettingOpportunity)
                    .where(BettingOpportunity.match_id == match.id)
                    .where(BettingOpportunity.status == "pending")
                    .order_by(BettingOpportunity.expected_value.desc())
                )
                match_opps = opps_result.scalars().all()

                # Build match report with all its singole
                singole = []
                for opp in match_opps:
                    singole.append({
                        "market": opp.market or "",
                        "outcome": opp.outcome or "",
                        "best_odds": float(opp.best_odds) if opp.best_odds else 0.0,
                        "expected_value": float(opp.expected_value) if opp.expected_value else 0.0,
                        "bookmaker": opp.bookmaker or "",
                    })

                # Only include match in report if it has singole OR if analysis was incomplete
                # Include research_metadata if available (from Whoscored + news fetch)
                research_meta = (match.raw_stats or {}).get("research_metadata", {}) if match.raw_stats else {}

                match_report = {
                    "match_name": match.display_name(),
                    "competition": match.competition.name if match.competition else "Unknown",
                    "analysis_status": match.analysis_status or "unknown",
                    "analysis_reason": match.analysis_reason,
                    "singole": singole,
                }

                # Add research metadata if available (transparency on data completeness)
                if research_meta:
                    match_report["research_metadata"] = research_meta

                matches_report.append(match_report)

            # Update pipeline record
            finished_at = datetime.now(timezone.utc)
            pipeline.status = "done"
            pipeline.matches_processed = len(matches)
            pipeline.opportunities_found = opportunities_found
            pipeline.finished_at = finished_at
            await db.commit()

            duration_s = (finished_at - started_at).total_seconds()

            # Notifica Telegram con tutte le singole per match
            if sport:
                from app.services.telegram_service import send_sport_analysis_report
                await send_sport_analysis_report(
                    sport=sport,
                    matches_report=matches_report,
                    duration_s=duration_s
                )
            else:
                # Fallback per pipeline generale (senza sport specifico)
                if opportunities_found == 0:
                    from app.services.telegram_service import send_no_bet_today
                    await send_no_bet_today(
                        matches_analysed=len(matches),
                        duration_s=duration_s,
                    )
                else:
                    from app.services.telegram_service import send_pipeline_summary
                    await send_pipeline_summary(
                        matches_processed=len(matches),
                        opportunities_found=total_opps,
                        duration_s=duration_s,
                    )

            return {
                "matches_processed": len(matches),
                "opportunities_found": opportunities_found,
                "duration_s": (finished_at - started_at).total_seconds(),
            }

        except Exception as exc:
            pipeline.status = "failed"
            pipeline.error = str(exc)
            pipeline.finished_at = datetime.now(timezone.utc)
            await db.commit()
            raise


# ── Bet settlement ────────────────────────────────────────────────────────────

@celery_app.task(name="app.workers.tasks.controlla", bind=True, max_retries=2)
def controlla(self):
    """CONTROLLA — Check finished matches and settle open bets."""
    logger.info("Task CONTROLLA started")
    try:
        result = _run(_settle_bets_async())
        logger.info("CONTROLLA done: %s", result)
        return result
    except Exception as exc:
        logger.error("CONTROLLA failed: %s", exc)
        raise self.retry(exc=exc, countdown=180)


async def _settle_bets_async() -> dict:
    from datetime import timedelta
    from sqlalchemy import select, update
    from app.db.base import AsyncSessionLocal
    from app.db.models.bet import Bet
    from app.db.models.match import Match
    from app.db.models.opportunity import BettingOpportunity
    from app.services.settlement_service import SettlementService

    async with AsyncSessionLocal() as db:
        svc = SettlementService(db)

        # Considera finita una partita la cui match_date è passata da almeno 2 ore,
        # oppure già marcata "finished". Questo risolve il bug per cui match.status
        # restava "upcoming" e le scommesse non venivano mai liquidate.
        cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
        result = await db.execute(
            select(Bet)
            .join(BettingOpportunity, BettingOpportunity.id == Bet.opportunity_id)
            .join(Match, Match.id == BettingOpportunity.match_id)
            .where(Bet.status == "open")
            .where(
                (Match.status == "finished") |
                (Match.match_date < cutoff)
            )
            .limit(50)
        )
        bets = result.scalars().all()
        settled = 0
        for bet in bets:
            ok = await svc.settle_bet(bet)
            if ok:
                settled += 1
        return {"settled": settled}



# ── Health check ──────────────────────────────────────────────────────────────

@celery_app.task(name="app.workers.tasks.run_health_check")
def run_health_check():
    """System health check — runs every 2 minutes."""
    _run(_health_check_async())


async def _health_check_async() -> None:
    from app.db.base import AsyncSessionLocal
    from app.services.health import run_health_check as _hc

    async with AsyncSessionLocal() as db:
        await _hc(db)


# ── CLV Updater ───────────────────────────────────────────────────────────────

@celery_app.task(name="app.workers.tasks.update_clv")
def update_clv():
    """
    Update Closing Line Value for recently settled bets.
    CLV = (closing_odds / placed_odds - 1) * 100
    We approximate closing odds as the latest fetched odds before match start.
    """
    _run(_update_clv_async())


async def _update_clv_async() -> None:
    """
    Calcola il CLV (Closing Line Value) rispetto a Pinnacle.

    CLV = (odds_piazzate / pinnacle_closing_odds - 1) × 100

    Positivo = hai preso una quota migliore di Pinnacle a chiusura → edge reale.
    Negativo = Pinnacle aveva già capito meglio di te → nessun edge.

    Se le quote Pinnacle a chiusura non sono disponibili, usa il bookmaker soft
    come fallback (meno significativo ma meglio di niente).
    """
    from sqlalchemy import select, and_
    from app.db.base import AsyncSessionLocal
    from app.db.models.bet import Bet
    from app.db.models.opportunity import BettingOpportunity
    from app.db.models.match import Match, MatchOdds

    PINNACLE_KEYS = {"pinnacle", "betfair_ex_eu"}

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Bet, BettingOpportunity, Match)
            .join(BettingOpportunity, BettingOpportunity.id == Bet.opportunity_id)
            .join(Match, Match.id == BettingOpportunity.match_id)
            .where(
                and_(
                    Bet.status.in_(["won", "lost"]),
                    Bet.closing_odds.is_(None),
                )
            )
            .limit(50)
        )
        rows = result.all()
        updated = 0

        for bet, opp, match in rows:
            closing_odds_val = None

            # 1. Prova prima con Pinnacle (benchmark sharp)
            for bk in PINNACLE_KEYS:
                pinnacle_result = await db.execute(
                    select(MatchOdds)
                    .where(
                        and_(
                            MatchOdds.match_id == opp.match_id,
                            MatchOdds.market == opp.market,
                            MatchOdds.outcome == opp.outcome,
                            MatchOdds.bookmaker == bk,
                            MatchOdds.fetched_at <= match.match_date,
                        )
                    )
                    .order_by(MatchOdds.fetched_at.desc())
                    .limit(1)
                )
                pinnacle_close = pinnacle_result.scalar_one_or_none()
                if pinnacle_close:
                    closing_odds_val = float(pinnacle_close.odds)
                    break

            # 2. Fallback: stesso bookmaker soft se Pinnacle non disponibile
            if closing_odds_val is None:
                soft_result = await db.execute(
                    select(MatchOdds)
                    .where(
                        and_(
                            MatchOdds.match_id == opp.match_id,
                            MatchOdds.market == opp.market,
                            MatchOdds.outcome == opp.outcome,
                            MatchOdds.bookmaker == opp.bookmaker,
                            MatchOdds.fetched_at <= match.match_date,
                        )
                    )
                    .order_by(MatchOdds.fetched_at.desc())
                    .limit(1)
                )
                soft_close = soft_result.scalar_one_or_none()
                if soft_close:
                    closing_odds_val = float(soft_close.odds)

            if closing_odds_val:
                bet.closing_odds = closing_odds_val
                # CLV vs Pinnacle: positivo = hai battuto il mercato sharp
                bet.clv = round((float(bet.odds) / closing_odds_val - 1) * 100, 4)
                updated += 1
                logger.debug(
                    "CLV bet %s: placed=%.3f close=%.3f clv=%+.2f%%",
                    bet.id, float(bet.odds), closing_odds_val, bet.clv,
                )

        if updated:
            await db.commit()
            logger.info("CLV updated for %d bets (benchmark: Pinnacle closing)", updated)

        # Update CLV blacklist for soft bookmakers with negative CLV
        blacklist_result = await db.execute(
            select(
                Bet.bookmaker,
                func.count(Bet.id).label("count"),
                func.avg(Bet.clv).label("avg_clv")
            )
            .where(
                and_(
                    Bet.status.in_(["won", "lost"]),
                    Bet.clv.isnot(None),
                    Bet.bookmaker.notin_(PINNACLE_KEYS)  # Soft bookmakers only
                )
            )
            .group_by(Bet.bookmaker)
            .having(func.count(Bet.id) >= 10)  # Min 10 settled bets
        )
        blacklist_rows = blacklist_result.all()

        blacklisted = set()
        for row in blacklist_rows:
            bookmaker = row[0]
            count = row[1]
            avg_clv = float(row[2]) if row[2] else 0.0

            # If avg CLV < -5%, add to blacklist
            if avg_clv < -5.0:
                blacklisted.add(bookmaker)
                logger.warning(
                    "🚫 CLV blacklist: %s (avg CLV: %+.2f%% on %d bets)",
                    bookmaker, avg_clv, count
                )

        # Save blacklist to Redis with 30-day TTL
        if blacklisted:
            import json
            import redis as _redis
            try:
                r = _redis.from_url(settings.redis_url_with_auth, decode_responses=True)
                r.setex("bookmaker:clv:blacklist", 86400 * 30, json.dumps(list(blacklisted)))
                logger.info("CLV blacklist updated in Redis: %s", blacklisted)
            except Exception as e:
                logger.error("Failed to update CLV blacklist in Redis: %s", e)


# ── Auto-expire "in_attesa" quando la partita inizia ─────────────────────────

@celery_app.task(name="app.workers.tasks.expire_waiting_opportunities")
def expire_waiting_opportunities():
    """
    Se una opportunità è rimasta 'in_attesa' e la partita è già iniziata
    → viene automaticamente segnata come rifiutata.
    Gira ogni 5 minuti insieme al monitor odds.
    """
    _run(_expire_waiting_async())


async def _expire_waiting_async() -> None:
    from sqlalchemy import select, and_
    from datetime import datetime, timezone
    from app.db.base import AsyncSessionLocal
    from app.db.models.opportunity import BettingOpportunity
    from app.db.models.match import Match

    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(BettingOpportunity, Match)
            .join(Match, Match.id == BettingOpportunity.match_id)
            .where(
                and_(
                    BettingOpportunity.status.in_(["pending", "in_attesa"]),
                    Match.match_date <= now,  # partita già iniziata
                )
            )
        )
        rows = result.all()
        expired_hold = 0
        expired_pending = 0

        for opp, match in rows:
            time_str = match.match_date.strftime("%H:%M") if match.match_date else "?"
            was_in_attesa = opp.status == "in_attesa"  # controlla PRIMA di cambiare
            opp.status = "expired"
            opp.rejection_reason = f"Scaduta automaticamente — partita iniziata alle {time_str}"
            if was_in_attesa:
                expired_hold += 1
            else:
                expired_pending += 1

        expired = expired_hold + expired_pending
        if expired:
            await db.commit()
            logger.info(
                "Auto-expire: %d pending + %d in_attesa scadute (partite iniziate)",
                expired_pending, expired_hold,
            )

            # Notifica solo per quelle in attesa (le pending scadono in silenzio)
            if expired_hold > 0:
                try:
                    from app.services.telegram_service import _send
                    await _send(
                        f"⏰ <b>{expired_hold} scommessa/e in attesa scaduta/e</b>\n"
                        f"La partita è iniziata senza che tu le abbia giocate.\n"
                        f"Segnate automaticamente come scadute."
                    )
                except Exception:
                    pass


# ── Odds Monitor (5 min) ──────────────────────────────────────────────────────

@celery_app.task(name="app.workers.tasks.monitor_odds_movement")
def monitor_odds_movement():
    """
    Detect significant odds movement (≥ 10%) for today's matches.
    Signals potential sharp money or late news.
    """
    _run(_monitor_odds_async())


async def _monitor_odds_async() -> None:
    from sqlalchemy import select, func, and_, update
    from datetime import date, timedelta as _td2
    from app.db.base import AsyncSessionLocal
    from app.db.models.match import Match, MatchOdds
    from app.db.models.opportunity import BettingOpportunity

    SHARP_BOOKMAKERS = {"pinnacle", "betfair_ex_eu"}

    async with AsyncSessionLocal() as db:
        today = date.today()
        now_utc = datetime.now(timezone.utc)
        result = await db.execute(
            select(Match)
            .where(
                and_(
                    Match.status == "scheduled",
                    func.date(Match.match_date) == today,
                    # Ignora partite già iniziate (nessun senso monitorare post-kick-off)
                    Match.match_date > now_utc,
                )
            )
        )
        matches = result.scalars().all()

        for match in matches:
            # Get first and last odds for each market/outcome
            first_result = await db.execute(
                select(MatchOdds)
                .where(MatchOdds.match_id == match.id)
                .order_by(MatchOdds.fetched_at.asc())
                .limit(50)
            )
            first_batch = first_result.scalars().all()

            last_result = await db.execute(
                select(MatchOdds)
                .where(MatchOdds.match_id == match.id)
                .order_by(MatchOdds.fetched_at.desc())
                .limit(50)
            )
            last_batch = last_result.scalars().all()

            # Compare first vs last: usa solo coppie con almeno 3h di distanza
            # (evita falsi positivi con righe duplicate dello stesso fetch)
            from datetime import timedelta as _td
            min_gap = _td(hours=3)

            # Deduplicato per (bookmaker, market, outcome) — prendi il più vecchio
            first_map: dict[tuple, tuple] = {}  # key → (odds, fetched_at)
            for o in first_batch:
                key = (o.bookmaker, o.market, o.outcome)
                if key not in first_map:
                    first_map[key] = (float(o.odds), o.fetched_at)

            # Prendi il più recente per ciascuna chiave
            last_map: dict[tuple, tuple] = {}
            for o in last_batch:
                key = (o.bookmaker, o.market, o.outcome)
                if key not in last_map:
                    last_map[key] = (float(o.odds), o.fetched_at)

            for key, (current, last_ts) in last_map.items():
                if key not in first_map:
                    continue
                opening, first_ts = first_map[key]
                # Gap minimo: evita confronto righe dello stesso fetch
                if first_ts and last_ts and (last_ts - first_ts) < min_gap:
                    continue
                if opening <= 0:
                    continue

                movement = abs(current - opening) / opening
                if movement < 0.10:
                    continue

                bookmaker, market, outcome = key
                direction = "down" if current < opening else "up"
                logger.info(
                    "ODDS MOVEMENT: %s — %s %s %s: %.2f→%.2f (%.1f%%, %s)",
                    match.display_name(), bookmaker, market,
                    outcome, opening, current, movement * 100, direction,
                )

                # ── Pinnacle movement confirmation ────────────────────────────
                # Se il movimento è su Pinnacle/Betfair (bookmaker sharp):
                #   - Odds scende (=quota cala): sharp money punta su quell'esito → CONFERMA
                #   - Odds sale (=quota aumenta): sharp money CONTRO quell'esito → RIFIUTA
                if bookmaker not in SHARP_BOOKMAKERS:
                    continue

                # Cerca opportunità pending per questo match+mercato+esito
                opp_result = await db.execute(
                    select(BettingOpportunity)
                    .where(
                        and_(
                            BettingOpportunity.match_id == match.id,
                            BettingOpportunity.market == market,
                            BettingOpportunity.outcome == outcome,
                            BettingOpportunity.status.in_(["pending", "in_attesa"]),
                        )
                    )
                )
                opps = opp_result.scalars().all()
                if not opps:
                    continue

                for opp in opps:
                    existing_votes = opp.consensus_votes or {}

                    if direction == "down":
                        # Pinnacle abbassa la quota → sharp money conferma il valore
                        # Boost affidabilità: aggiorna flag in consensus_votes
                        existing_votes["pinnacle_movement"] = "confirmed"
                        existing_votes["pinnacle_movement_pct"] = round(movement * 100, 1)
                        new_reliability = min(0.92, float(existing_votes.get("reliability", 0.5)) * 1.10)
                        existing_votes["reliability"] = round(new_reliability, 4)

                        await db.execute(
                            update(BettingOpportunity)
                            .where(BettingOpportunity.id == opp.id)
                            .values(consensus_votes=existing_votes)
                        )
                        logger.info(
                            "Pinnacle CONFERMA: %s %s %s — reliability +10%% → %.0f%%",
                            match.display_name(), market, outcome, new_reliability * 100,
                        )
                        try:
                            from app.services.telegram_service import _send
                            await _send(
                                f"📈 <b>Pinnacle conferma il valore</b>\n"
                                f"{match.display_name()} — {market} {outcome}\n"
                                f"Quota Pinnacle: {opening:.2f} → {current:.2f} "
                                f"({movement*100:.1f}% in discesa)\n"
                                f"I soldi sharp stanno entrando nella stessa direzione della tua scommessa."
                            )
                        except Exception as e:
                            logger.warning("Telegram movement alert failed: %s", e)

                    else:
                        # Pinnacle alza la quota → sharp money contro il valore
                        # Rifiuta automaticamente l'opportunità
                        existing_votes["pinnacle_movement"] = "rejected"
                        existing_votes["pinnacle_movement_pct"] = round(movement * 100, 1)
                        rejection = (
                            f"Pinnacle ha mosso la quota +{movement*100:.1f}% "
                            f"({opening:.2f}→{current:.2f}) — soldi sharp contro l'esito"
                        )
                        await db.execute(
                            update(BettingOpportunity)
                            .where(BettingOpportunity.id == opp.id)
                            .values(
                                status="rejected",
                                rejection_reason=rejection,
                                consensus_votes=existing_votes,
                            )
                        )
                        logger.info(
                            "Pinnacle RIFIUTA: %s %s %s — auto-rejected",
                            match.display_name(), market, outcome,
                        )
                        try:
                            from app.services.telegram_service import _send
                            await _send(
                                f"⚠️ <b>Pinnacle si muove contro</b>\n"
                                f"{match.display_name()} — {market} {outcome}\n"
                                f"Quota Pinnacle: {opening:.2f} → {current:.2f} "
                                f"({movement*100:.1f}% in salita)\n"
                                f"Opportunità annullata automaticamente — i soldi sharp non confermano il valore."
                            )
                        except Exception as e:
                            logger.warning("Telegram rejection alert failed: %s", e)

        await db.commit()


# ── CLV Auto-calibration (weekly) ────────────────────────────────────────────

@celery_app.task(name="app.workers.tasks.calibrate_clv")
def calibrate_clv():
    """
    Analisi settimanale del CLV storico per auto-calibrare le soglie del pipeline.

    Calcola:
    - CLV medio su ultimi 30 bet liquidati
    - Per bookmaker: media CLV, conta bet positivi vs negativi
    - Se CLV medio < 0: avvisa che il threshold EV è troppo basso
    - Se un bookmaker ha CLV medio < -3%: blacklistato temporaneamente (Redis, 7gg)
    - Salva risultato in Redis per consultazione rapida
    """
    _run(_calibrate_clv_async())


async def _calibrate_clv_async() -> None:
    from sqlalchemy import select, func
    from app.db.base import AsyncSessionLocal
    from app.db.models.bet import Bet
    from app.db.models.opportunity import BettingOpportunity
    from collections import defaultdict
    import json

    async with AsyncSessionLocal() as db:
        # Prendi gli ultimi 50 bet liquidati con CLV disponibile
        result = await db.execute(
            select(Bet, BettingOpportunity)
            .join(BettingOpportunity, BettingOpportunity.id == Bet.opportunity_id)
            .where(
                Bet.status.in_(["won", "lost"]),
                Bet.clv.isnot(None),
            )
            .order_by(Bet.settled_at.desc())
            .limit(50)
        )
        rows = result.all()

        if len(rows) < 5:
            logger.info("CLV calibration: meno di 5 bet con CLV — skip (insufficiente)")
            return

        # Statistiche globali
        all_clv = [float(bet.clv) for bet, _ in rows]
        avg_clv = sum(all_clv) / len(all_clv)
        positive_clv = sum(1 for c in all_clv if c > 0)
        win_rate_clv = positive_clv / len(all_clv)

        # Statistiche per bookmaker
        bk_clv: dict[str, list[float]] = defaultdict(list)
        for bet, opp in rows:
            bk_clv[opp.bookmaker].append(float(bet.clv))

        bk_stats: dict[str, dict] = {}
        blacklisted: list[str] = []
        for bk, clv_list in bk_clv.items():
            bk_avg = sum(clv_list) / len(clv_list)
            bk_stats[bk] = {
                "avg_clv": round(bk_avg, 3),
                "n_bets": len(clv_list),
                "pct_positive": round(sum(1 for c in clv_list if c > 0) / len(clv_list), 2),
            }
            # Blacklist: bookmaker con CLV medio < -3% su almeno 5 scommesse
            if bk_avg < -3.0 and len(clv_list) >= 5:
                blacklisted.append(bk)

        calibration_data = {
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "n_bets": len(rows),
            "avg_clv": round(avg_clv, 3),
            "win_rate_clv": round(win_rate_clv, 2),
            "bookmaker_stats": bk_stats,
            "blacklisted_bookmakers": blacklisted,
            "recommendation": _clv_recommendation(avg_clv, win_rate_clv),
        }

        # Salva in Redis
        import redis as redis_lib
        from app.config import settings as _cfg
        try:
            r = redis_lib.from_url(_cfg.redis_url_with_auth, decode_responses=True)
            r.setex("clv:calibration:latest", 86400 * 8, json.dumps(calibration_data))

            # Blacklist bookmaker: chiave Redis per 7 giorni
            for bk in blacklisted:
                r.setex(f"clv:blacklist:{bk}", 86400 * 7, "1")
                logger.warning("CLV blacklist: %s (avg CLV: %.1f%%) per 7gg", bk, bk_stats[bk]["avg_clv"])
        except Exception as exc:
            logger.error("CLV calibration: Redis save failed: %s", exc)

        logger.info(
            "CLV calibration: %d bet, avg CLV=%+.2f%%, win_rate_clv=%.0f%%, blacklisted=%s",
            len(rows), avg_clv, win_rate_clv * 100, blacklisted,
        )

        # ── Performance agenti (Brier score) ─────────────────────────────────
        from app.db.models.agent import AgentScore
        agent_score_rows = (await db.execute(
            select(AgentScore).order_by(AgentScore.brier_score.asc())
        )).scalars().all()

        # Notifica Telegram con il report settimanale completo
        try:
            from app.services.telegram_service import _send
            bk_lines = "\n".join(
                f"  {'🔴' if b in blacklisted else '🟢'} {b}: CLV {bk_stats[b]['avg_clv']:+.1f}% ({bk_stats[b]['n_bets']} bet)"
                for b in sorted(bk_stats, key=lambda x: bk_stats[x]['avg_clv'])
            )

            # Sezione agenti
            agent_lines = ""
            if agent_score_rows:
                lines = []
                for s in agent_score_rows:
                    if s.total_predictions < 3:
                        continue  # troppo pochi dati per essere significativo
                    acc = s.correct_predictions / s.total_predictions if s.total_predictions else 0
                    bs  = float(s.brier_score)
                    # Brier score: 0 = perfetto, 0.25 = random, >0.25 = peggio del caso
                    if bs < 0.15:
                        icon = "🟢"
                    elif bs < 0.22:
                        icon = "🟡"
                    else:
                        icon = "🔴"
                    lines.append(
                        f"  {icon} {s.agent_name}: Brier {bs:.3f} · "
                        f"accuratezza {acc:.0%} · peso {float(s.weight):.2f} "
                        f"({s.total_predictions} pred)"
                    )
                if lines:
                    agent_lines = "\n<b>Performance agenti AI:</b>\n" + "\n".join(lines) + "\n"

            msg = (
                f"🧠 <b>Report settimanale PEPODDS21</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"Analisi su {len(rows)} scommesse liquidate\n\n"
                f"<b>CLV vs Pinnacle</b>\n"
                f"  Media: <b>{avg_clv:+.2f}%</b>\n"
                f"  Battuto il mercato: <b>{win_rate_clv*100:.0f}%</b> delle volte\n\n"
                f"<b>Per bookmaker:</b>\n{bk_lines}\n"
                f"{agent_lines}"
                f"\n💡 {calibration_data['recommendation']}"
            )
            if blacklisted:
                msg += f"\n\n⛔ <b>Blacklistati 7gg:</b> {', '.join(blacklisted)}"
            await _send(msg)
        except Exception as exc:
            logger.warning("CLV calibration telegram failed: %s", exc)


def _clv_recommendation(avg_clv: float, win_rate_clv: float) -> str:
    """Genera una raccomandazione testuale basata sul CLV storico."""
    if avg_clv >= 3.0 and win_rate_clv >= 0.60:
        return "Sistema calibrato perfettamente — continua così."
    elif avg_clv >= 1.0:
        return "Edge positivo. Considera di abbassare la soglia EV al 3% per trovare più opportunità."
    elif avg_clv >= 0.0:
        return "Edge marginale. Threshold EV attuale (4%) è nella norma. Monitora nei prossimi 30 bet."
    elif avg_clv >= -2.0:
        return "CLV leggermente negativo — potresti stare pagando il vig. Considera di alzare la soglia EV al 5%."
    else:
        return "CLV chiaramente negativo. Alza la soglia EV al 6%+ e controlla i bookmaker in rosso."


# ── Fetch Complete Sport Data (On-Demand via Telegram) ───────────────────────

@celery_app.task(name="app.workers.tasks.fetch_complete_sport_data", bind=True, max_retries=2)
def fetch_complete_sport_data(self, sport: str | None = None):
    """
    Fetch COMPLETO per uno sport: quote + stats arricchiti.
    Chiamato on-demand dai comandi Telegram: /ricerca, /ricerca_calcio, /ricerca_nba, /ricerca_tennis.

    Args:
        sport: "football", "basketball", "tennis", o None per tutti gli sport.

    Returns:
        {"odds": {...}, "stats": {...}}
    """
    logger.info("Task fetch_complete_sport_data started (sport=%s)", sport or "all")
    try:
        # Determine lookahead window: 20h for tutti gli sport (focused + evita intasamento)
        hours_lookahead = 20

        # Step 1: Sync competizioni SEMPRE per scoprire nuovi tornei (indipendentemente da sport)
        sync_result = _run(_sync_competitions_async())
        logger.info("sync_competitions completed: %s", sync_result)

        # Step 2: Fetch odds per competizioni attive dello sport
        odds_result = _run(fetch_all_odds_async(sport))
        logger.info("fetch_all_odds completed: %s", odds_result)

        # Step 3: Fetch stats enrichment (forma, infortuni, H2H, rankings)
        # Usa 20h per ricerche sport-specifiche, 48h per ricerca globale
        stats_result = _run(fetch_upcoming_stats_async(sport, hours_lookahead=hours_lookahead))
        logger.info("fetch_upcoming_stats completed (sport=%s, lookahead=%dh): %s", sport or "all", hours_lookahead, stats_result)

        return {
            "sport": sport or "all",
            "odds_count": sum(odds_result.values()),
            "stats_updated": stats_result.get("updated", 0),
        }
    except Exception as exc:
        logger.error("fetch_complete_sport_data failed (sport=%s): %s", sport, exc)
        raise self.retry(exc=exc, countdown=120)


# ── Telegram Polling ──────────────────────────────────────────────────────────

@celery_app.task(name="app.workers.tasks.poll_telegram_updates")
def poll_telegram_updates():
    """
    Polling Telegram invece di webhook (non serve HTTPS).
    Gira ogni 10 secondi, recupera aggiornamenti e li processa.
    """
    _run(_poll_telegram_async())


async def _poll_telegram_async() -> None:
    import httpx
    from app.config import settings

    token = settings.telegram_bot_token
    if not token:
        return

    # Connessione Redis (usata sia per lock che per offset)
    import redis as redis_lib
    from app.config import settings as _cfg
    _redis = redis_lib.from_url(_cfg.redis_url_with_auth, decode_responses=True)

    # Lock distribuito: solo UN worker alla volta può fare getUpdates.
    # TTL = 9s (< interval 10s) → se il task si blocca, il lock scade da solo.
    lock_key = "telegram:polling:lock"
    acquired = _redis.set(lock_key, "1", nx=True, ex=9)
    if not acquired:
        return  # un altro worker sta già facendo polling

    # Leggi l'offset da Redis (persistente tra restart del container)
    offset = 0
    try:
        _offset_val = _redis.get("telegram:polling:offset")
        offset = int(_offset_val) if _offset_val else 0
    except Exception:
        offset = 0

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params={"offset": offset, "limit": 10, "timeout": 5},
            )
            if not resp.is_success:
                return
            data = resp.json()
            updates = data.get("result", [])
    except Exception as exc:
        logger.warning("Telegram polling error: %s", exc)
        return

    if not updates:
        return

    # Processa ogni update attraverso il webhook handler
    async with httpx.AsyncClient(timeout=10.0) as client:
        for update in updates:
            try:
                await client.post(
                    "http://backend:8000/telegram/webhook",
                    json=update,
                    headers={"Content-Type": "application/json"},
                )
            except Exception as exc:
                logger.warning("Telegram forward error for update %s: %s", update.get("update_id"), exc)

    # Aggiorna offset in Redis (TTL 90 giorni per sicurezza)
    last_id = updates[-1].get("update_id", 0)
    try:
        _redis.setex("telegram:polling:offset", 86400 * 90, str(last_id + 1))
    except Exception:
        pass

    # Rilascia il lock
    try:
        _redis.delete(lock_key)
    except Exception:
        pass

    logger.info("Telegram polling: processed %d updates (last_id=%d)", len(updates), last_id)
