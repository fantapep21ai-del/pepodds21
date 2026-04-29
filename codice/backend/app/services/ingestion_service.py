"""
Ingestion service — orchestrates fetching + DB upsert.

Called by Celery tasks. Each method is a single unit of work:
fetch raw data → parse → upsert to DB → return counts.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, update, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.match import Competition, Match, MatchOdds
from app.services.odds_fetcher import OddsAPIClient, parse_odds_response, parse_player_props_response
from app.services.stats_fetcher import FootballStatsClient, TennisStatsClient

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────────
# COMPETITION FILTERS — Sport-specific league/tournament restrictions
# ────────────────────────────────────────────────────────────────────────────────

COMPETITION_FILTERS = {
    "football": {
        "leagues": [
            "Serie A", "Bundesliga", "Champions League", "Europa League",
            "Premier League", "La Liga", "Ligue 1"
        ]
    },
    "tennis": {
        "tournaments": [
            # Grand Slams
            "Australian Open", "French Open", "Wimbledon", "US Open",
            # Masters 1000
            "Indian Wells", "Miami", "Monte Carlo", "Rome", "Madrid",
            "Canada", "Cincinnati", "Shanghai", "Paris",
            "ATP 1000",  # Generic masters fallback
        ],
        "gender": "male",  # Singles only
    },
    "basketball": {
        "leagues": ["NBA", "NBA Playoffs"],
        "include_playoffs": True,
        "playoff_until": "2026-06-30",  # Seasonal cutoff
    }
}

LEAGUE_ALIASES = {
    "football": {
        "Serie A": ["Serie A", "Serie A TIM", "Serie A 2023", "Serie A 2024", "Serie A 2025"],
        "Bundesliga": ["Bundesliga", "1. Bundesliga", "German Bundesliga"],
        "Champions League": ["UEFA Champions League", "Champions League", "CL"],
        "Europa League": ["UEFA Europa League", "Europa League", "EL"],
        "Premier League": ["English Premier League", "Premier League", "EPL"],
        "La Liga": ["La Liga", "La Liga EA Sports", "Spanish La Liga"],
        "Ligue 1": ["Ligue 1", "Ligue 1 Uber Eats"],
    },
    "tennis": {
        "Australian Open": ["Australian Open", "AO", "Australian"],
        "French Open": ["French Open", "Roland Garros", "FO"],
        "Wimbledon": ["Wimbledon", "Championships", "W"],
        "US Open": ["US Open", "USO", "US"],
        "Indian Wells": ["Indian Wells", "BNP Paribas Open"],
        "Miami": ["Miami", "Miami Open"],
        "Monte Carlo": ["Monte Carlo", "Monte-Carlo Masters"],
        "Rome": ["Rome", "Italian Open"],
        "Madrid": ["Madrid", "Madrid Masters"],
        "Canada": ["Canada", "Canada Masters", "Canadian Open"],
        "Cincinnati": ["Cincinnati", "Cincinnati Masters"],
        "Shanghai": ["Shanghai", "Shanghai Masters"],
        "Paris": ["Paris", "Paris Masters"],
    },
    "basketball": {
        "NBA": ["NBA", "National Basketball Association"],
        "Playoffs": ["NBA Playoffs", "Playoff", "Finals"],
    }
}


def normalize_league_name(raw_name: str, sport: str) -> str | None:
    """
    Match raw API league name against known leagues/tournaments.
    Returns canonical league name or None if not recognized.
    """
    if not raw_name or sport not in LEAGUE_ALIASES:
        return None

    raw_lower = raw_name.lower()
    for canonical, aliases in LEAGUE_ALIASES[sport].items():
        if any(alias.lower() in raw_lower for alias in aliases):
            return canonical
    return None


# ────────────────────────────────────────────────────────────────────────────────
# SPORT KEY MAPPING — The Odds API endpoint mapping per competition
# ────────────────────────────────────────────────────────────────────────────────

SPORT_KEY_MAPPING = {
    "football": {
        "Serie A": "soccer_serie_a",
        "Bundesliga": "soccer_germany_bundesliga",
        "Champions League": "soccer_uefa_champs_league",
        "Europa League": "soccer_uefa_europa_league",
        "Premier League": "soccer_epl",
        "La Liga": "soccer_spain_la_liga",
        "Ligue 1": "soccer_france_ligue_one",
    },
    "basketball": {
        "NBA": "basketball_nba",
    },
    "tennis": {
        # Tennis uses ATP/WTA splits, not tournament-specific keys in The Odds API
        "Australian Open": "tennis_atp",
        "French Open": "tennis_atp",
        "Wimbledon": "tennis_atp",
        "US Open": "tennis_atp",
        "Indian Wells": "tennis_atp",
        "Miami": "tennis_atp",
        "Monte Carlo": "tennis_atp",
        "Rome": "tennis_atp",
        "Madrid": "tennis_atp",
        "Canada": "tennis_atp",
        "Cincinnati": "tennis_atp",
        "Shanghai": "tennis_atp",
        "Paris": "tennis_atp",
        "ATP 1000": "tennis_atp",
    }
}


def is_league_allowed(league_name: str, sport: str) -> bool:
    """
    Check if a league/tournament is allowed for a specific sport.
    """
    if sport not in COMPETITION_FILTERS:
        return True  # No filter = allow all

    filter_config = COMPETITION_FILTERS[sport]
    allowed_leagues = filter_config.get("leagues", []) + filter_config.get("tournaments", [])

    return league_name in allowed_leagues


def get_sport_key_for_competition(league_name: str, sport: str) -> str | None:
    """
    Get The Odds API sport_key for a given competition.
    Returns None if no mapping exists.
    """
    if sport not in SPORT_KEY_MAPPING:
        return None
    return SPORT_KEY_MAPPING[sport].get(league_name)


class IngestionService:

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.odds_client = OddsAPIClient()
        self.football_client = FootballStatsClient()
        self.tennis_client = TennisStatsClient()

    # ── Odds ──────────────────────────────────────────────────────────────────

    async def ingest_odds_for_competition(self, competition: Competition) -> int:
        """
        Fetch + upsert odds for one competition.
        Returns number of MatchOdds rows inserted.
        """
        if not competition.odds_api_key:
            logger.warning("Competition %s has no odds_api_key — skipping", competition.name)
            return 0

        raw_events = await self.odds_client.fetch_odds(competition.odds_api_key)
        matches_data, odds_data = parse_odds_response(
            raw_events,
            str(competition.id),
            competition.sport,
        )

        # Upsert matches (insert or update match_date / status only)
        for m in matches_data:
            stmt = (
                pg_insert(Match)
                .values(
                    external_id=m["external_id"],
                    competition_id=m["competition_id"],
                    sport=m["sport"],
                    home_team=m["home_team"],
                    away_team=m["away_team"],
                    match_date=m["match_date"],
                    status=m["status"],
                )
                .on_conflict_do_update(
                    index_elements=["external_id"],
                    set_={"match_date": m["match_date"], "status": m["status"]},
                )
            )
            await self.db.execute(stmt)

        await self.db.flush()

        # Resolve external_id → match.id map
        ext_ids = [m["external_id"] for m in matches_data]
        result = await self.db.execute(
            select(Match.id, Match.external_id).where(Match.external_id.in_(ext_ids))
        )
        ext_to_id: dict[str, str] = {row.external_id: str(row.id) for row in result}

        # Stale check — skip fetched_at older than threshold
        now = datetime.now(timezone.utc)

        inserted = 0
        for o in odds_data:
            match_id = ext_to_id.get(o["match_external_id"])
            if not match_id:
                continue
            await self.db.execute(
                pg_insert(MatchOdds).values(
                    match_id=match_id,
                    bookmaker=o["bookmaker"],
                    market=o["market"],
                    outcome=o["outcome"],
                    odds=o["odds"],
                    fetched_at=o["fetched_at"],
                    is_live=o["is_live"],
                )
            )
            inserted += 1

        await self.db.commit()

        # ── Pulizia righe vecchie: mantieni solo ultimi 7 giorni per ogni match ──
        # Previene crescita illimitata della tabella (ogni fetch aggiunge righe duplicate).
        # 7 giorni sono sufficienti per CLV (closing odds) e monitor movimenti.
        if ext_ids:
            stale_cutoff = datetime.now(timezone.utc) - timedelta(days=7)
            match_ids_list = list(ext_to_id.values())
            await self.db.execute(
                delete(MatchOdds)
                .where(MatchOdds.match_id.in_(match_ids_list))
                .where(MatchOdds.fetched_at < stale_cutoff)
            )
            await self.db.commit()

        # ── Player props: abilitati per football e basketball ────────────────────
        # Consumano 1 req per ogni match — sicuro perché fetch è on-demand via Telegram.
        # Sistema fetch manuale: 2-3 volte al giorno, controllato dall'utente.
        sport_norm = competition.sport.lower()
        tier = getattr(competition, "tier", "B")

        # Player props ABILITATI per calcio e basket — disabilitato per tennis (sport 1v1)
        _enable_props = sport_norm in ("football", "basketball")
        if _enable_props:
            for m in matches_data[:5]:
                try:
                    raw_props = await self.odds_client.fetch_player_props(
                        competition.odds_api_key, m["external_id"]
                    )
                    if not raw_props:
                        continue
                    props_odds = parse_player_props_response(raw_props, m["external_id"])
                    match_id = ext_to_id.get(m["external_id"])
                    if not match_id:
                        continue
                    for o in props_odds:
                        await self.db.execute(
                            pg_insert(MatchOdds).values(
                                match_id=match_id,
                                bookmaker=o["bookmaker"],
                                market=o["market"],
                                outcome=o["outcome"],
                                odds=o["odds"],
                                fetched_at=o["fetched_at"],
                                is_live=False,
                            )
                        )
                        inserted += 1
                    await self.db.commit()
                except Exception as exc:
                    logger.warning("Player props ingestion failed for event %s: %s", m["external_id"], exc)

        logger.info(
            "Ingested odds for %s — %d matches, %d odds rows (incl. player props)",
            competition.name, len(matches_data), inserted,
        )
        return inserted

    async def ingest_all_odds(self, sport: str | None = None) -> dict[str, int]:
        """
        Fetch odds per competizioni attive, opzionalmente filtrate per sport.

        Se sport è None: fetch TUTTI gli sport (legacy).
        Se sport è "football", "basketball", o "tennis": fetch SOLO quello sport.

        Incluse tutte le competizioni dello sport con:
        - Partite scheduled nelle prossime 72h
        - Partite nelle ultime 2 settimane (ancora in stagione)
        - Competizioni senza nessuna partita (nuovo torneo → primo fetch)
        """
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        window_past   = now - timedelta(days=14)
        window_future = now + timedelta(hours=72)

        # Costruisci query base
        query_active = (
            select(Competition)
            .join(Match, Match.competition_id == Competition.id)
            .where(Match.match_date >= window_past)
            .where(Match.match_date <= window_future)
            .distinct()
        )

        # Filtra per sport se specificato
        if sport:
            query_active = query_active.where(Competition.sport == sport.lower())

        result_active = await self.db.execute(query_active)
        active_comps = result_active.scalars().all()
        active_ids   = {c.id for c in active_comps}

        # Competizioni senza nessuna partita (mai fetchate)
        query_new = (
            select(Competition)
            .outerjoin(Match, Match.competition_id == Competition.id)
            .where(Match.id.is_(None))
        )

        if sport:
            query_new = query_new.where(Competition.sport == sport.lower())

        result_new = await self.db.execute(query_new)
        new_comps = [c for c in result_new.scalars().all() if c.id not in active_ids]

        to_fetch = active_comps + new_comps  # NESSUN LIMITE se sport-specific

        # ── APPLY LEAGUE FILTERS (sport-specific) ──
        if sport:
            filtered_comps = []
            for comp in to_fetch:
                canonical_league = normalize_league_name(comp.name, sport)
                if canonical_league and is_league_allowed(canonical_league, sport):
                    filtered_comps.append(comp)
                else:
                    logger.debug(
                        "Skipping competition %s (sport=%s): not in allowed list",
                        comp.name, sport
                    )
            to_fetch = filtered_comps

        logger.info(
            "ingest_all_odds (sport=%s): %d attive + %d nuove → %d after filters",
            sport or "all", len(active_comps), len(new_comps), len(to_fetch),
        )

        counts: dict[str, int] = {}
        for comp in to_fetch:
            try:
                # Get canonical league name and sport_key
                canonical_league = normalize_league_name(comp.name, comp.sport)
                sport_key = get_sport_key_for_competition(
                    canonical_league or comp.name, comp.sport
                )

                if not sport_key:
                    logger.warning(
                        "No sport_key mapping for %s (sport=%s) — skipping",
                        comp.name, comp.sport
                    )
                    counts[comp.name] = -1
                    continue

                # Temporarily set odds_api_key if not already set
                if not comp.odds_api_key:
                    comp.odds_api_key = sport_key

                counts[comp.name] = await self.ingest_odds_for_competition(comp)
            except Exception as exc:
                logger.error("Odds ingestion failed for %s: %s", comp.name, exc)
                counts[comp.name] = -1
        return counts

    # ── Football Stats ────────────────────────────────────────────────────────

    async def ingest_football_stats(self, match: Match) -> bool:
        """
        Fetch and store stats for a finished football match.
        Writes to Match.raw_stats (JSONB).
        """
        if not match.external_id:
            return False

        try:
            fixture_id = int(match.external_id.split("_")[-1])
        except (ValueError, AttributeError):
            logger.warning("Cannot parse fixture_id from external_id=%s", match.external_id)
            return False

        try:
            raw = await self.football_client.get_fixture_stats(fixture_id)
            injuries = await self.football_client.get_injuries(fixture_id)
            parsed = self.football_client.parse_fixture_stats(raw)
            parsed["injuries"] = injuries
        except Exception as exc:
            logger.error("Football stats fetch failed for fixture %s: %s", fixture_id, exc)
            return False

        await self.db.execute(
            update(Match)
            .where(Match.id == match.id)
            .values(raw_stats=parsed, updated_at=datetime.now(timezone.utc))
        )
        await self.db.commit()
        return True

    # ── Tennis Stats ─────────────────────────────────────────────────────────

    async def ingest_tennis_upcoming(self, competition: Competition) -> int:
        """Ingest upcoming tennis matches from Tennis Live Data API."""
        tour = "wta" if "wta" in competition.name.lower() else "atp"
        try:
            upcoming = await self.tennis_client.get_upcoming_matches(tour)
        except Exception as exc:
            logger.error("Tennis fetch failed: %s", exc)
            return 0

        inserted = 0
        for match_data in upcoming:
            player_a = match_data.get("player1", {}).get("full_name") or match_data.get("home")
            player_b = match_data.get("player2", {}).get("full_name") or match_data.get("away")
            ext_id = str(match_data.get("id", ""))
            start_time = match_data.get("start_at") or match_data.get("date")

            if not ext_id or not player_a:
                continue

            stmt = (
                pg_insert(Match)
                .values(
                    external_id=f"tennis_{ext_id}",
                    competition_id=str(competition.id),
                    sport="tennis",
                    player_a=player_a,
                    player_b=player_b,
                    match_date=start_time,
                    status="scheduled",
                )
                .on_conflict_do_update(
                    index_elements=["external_id"],
                    set_={"match_date": start_time, "status": "scheduled"},
                )
            )
            await self.db.execute(stmt)
            inserted += 1

        await self.db.commit()
        return inserted
