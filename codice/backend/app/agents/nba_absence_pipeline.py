"""
NBA Absence Impact Pipeline — analisi completa quando un giocatore è confermato OUT.

Quando viene rilevata un'assenza confermata (OUT / Doubtful):

  1. Identifica la squadra del giocatore OUT e la partita di stasera
  2. Trova tutti i compagni di squadra con quote disponibili oggi
  3. Per ogni compagno: calcola TUTTE le stats rilevanti
       - Medie stagionali normali (con il giocatore OUT in squadra)
       - Medie nelle partite senza quel giocatore (da Dunkest)
       - Usage adjustment: il compagno assorbe X% di usage in più?
       - Hit rate su ogni linea di prop corrente
  4. Analizza il matchup difensivo dell'avversario per posizione
       - "exploitable" → aumenta la probabilità over
       - "tough" → diminuisce
  5. Genera BettingOpportunity per ogni prop con EV > soglia
  6. Invia Telegram alert con panoramica completa

Soglie (conservative, mercato props già meno efficiente nelle notizie assenze):
  MIN_EV_ABSENCE    = 0.05   # 5% EV minimo (soglia abbassata: contesto favorevole)
  MIN_HIT_RATE      = 0.52   # 52% per absence-boosted props
  MIN_SAMPLE_ABSENCE = 5     # almeno 5 partite senza il compagno
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.match import Match, MatchOdds
from app.db.models.opportunity import BettingOpportunity
from app.services.dunkest_client import DunkestClient, find_player_id, MARKET_TO_STAT
from app.services.nba_matchup_client import NBAMatchupClient, get_player_position
from app.agents.player_props_pipeline import (
    PLAYER_PROP_MARKETS,
    COMBO_MARKETS,
    SHARP_PROP_BOOKMAKERS,
    MIN_SAMPLE_SIZE,
    MAX_ODDS,
    MIN_ODDS,
)

logger = logging.getLogger(__name__)

# ── Soglie specifiche per absence props ──────────────────────────────────────
MIN_EV_ABSENCE     = 0.05   # più basso: il contesto informativo è un edge di per sé
MIN_HIT_ABSENCE    = 0.52   # meno conservativo perché abbiamo il segnale di assenza
MIN_SAMPLE_ABSENCE = 5      # sample minimo nelle partite senza il compagno

# Statistiche da analizzare per ogni giocatore
ALL_STAT_KEYS = ["pts", "reb", "ast", "tpm", "blk", "stl"]

# Stat → market mapping
STAT_TO_MARKET: dict[str, str] = {
    "pts": "player_points",
    "reb": "player_rebounds",
    "ast": "player_assists",
    "tpm": "player_threes",
    "blk": "player_blocks",
    "stl": "player_steals",
}


async def run_absence_analysis(
    absent_player_name: str,
    absent_team: str,
    db: AsyncSession,
    force_all_stats: bool = True,
) -> dict:
    """
    Pipeline principale: analisi completa dell'impatto di un'assenza NBA.

    Args:
        absent_player_name: nome del giocatore assente (es. "LeBron James")
        absent_team:        squadra del giocatore (es. "Los Angeles Lakers")
        db:                 sessione DB
        force_all_stats:    se True, analizza tutti i mercati stat anche senza quote correnti

    Returns:
        {
          "absent_player": str,
          "team": str,
          "match": str | None,
          "teammates_analysed": int,
          "opportunities_found": int,
          "teammate_reports": list[dict],
        }
    """
    logger.info("ABSENCE PIPELINE: %s (%s) confermato OUT", absent_player_name, absent_team)

    # ── 1. Trova la partita di stasera per questa squadra ────────────────────
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=16)

    match = await _find_tonight_match(absent_team, db, now, window_end)
    if not match:
        logger.info("Absence: nessuna partita trovata stasera per %s", absent_team)
        return {
            "absent_player":      absent_player_name,
            "team":               absent_team,
            "match":              None,
            "teammates_analysed": 0,
            "opportunities_found": 0,
            "teammate_reports":   [],
        }

    logger.info(
        "Absence: partita trovata → %s (kickoff %s)",
        match.display_name(), match.match_date,
    )

    # ── 2. Trova l'ID Dunkest del giocatore assente ──────────────────────────
    absent_player_id = find_player_id(absent_player_name)
    if not absent_player_id:
        logger.warning("Absence: %s non trovato in Dunkest — matchup analysis only", absent_player_name)

    # ── 3. Carica quote player props della partita ───────────────────────────
    freshness = now.replace(hour=0, minute=0, second=0, microsecond=0)
    props_result = await db.execute(
        select(MatchOdds)
        .where(MatchOdds.match_id == match.id)
        .where(MatchOdds.market.in_(PLAYER_PROP_MARKETS))
        .where(MatchOdds.fetched_at >= freshness)
        .order_by(MatchOdds.fetched_at.desc())
        .limit(1000)
    )
    all_props: list[MatchOdds] = props_result.scalars().all()

    # ── 4. Raggruppa props per giocatore ────────────────────────────────────
    # {player_name → {(market, direction, line) → {bk: odds}}}
    player_props: dict[str, dict] = {}
    for row in all_props:
        from app.services.dunkest_client import parse_player_prop_outcome
        player_name, direction, line = parse_player_prop_outcome(row.outcome, row.market)
        if not player_name or not direction or line is None:
            continue
        if player_name not in player_props:
            player_props[player_name] = {}
        key = (row.market, direction, line)
        if key not in player_props[player_name]:
            player_props[player_name][key] = {}
        existing = player_props[player_name][key].get(row.bookmaker, 0.0)
        player_props[player_name][key][row.bookmaker] = max(existing, float(row.odds))

    logger.info(
        "Absence: %d giocatori con props in %s",
        len(player_props), match.display_name(),
    )

    # ── 5. Carica ranking difensivi ─────────────────────────────────────────
    matchup_client = NBAMatchupClient()
    defense_data = await matchup_client.get_league_defense_rankings()
    opposing_team = _get_opposing_team(match, absent_team)

    # ── 6. Analizza ogni compagno di squadra ────────────────────────────────
    dunkest = DunkestClient()
    games_cache: dict[int, list[dict]] = {}
    teammate_reports: list[dict] = []
    opps_found = 0

    for player_name, props in player_props.items():
        # Skip il giocatore assente stesso
        if absent_player_name.lower() in player_name.lower():
            continue

        player_id = find_player_id(player_name)
        if not player_id:
            continue

        # Cache game log
        if player_id not in games_cache:
            games = await dunkest.get_player_games(player_id)
            games_cache[player_id] = games
        else:
            games = games_cache[player_id]

        if not games:
            continue

        # ── Stats senza il giocatore assente ──────────────────────────────
        impact: dict | None = None
        if absent_player_id:
            # Simula partite senza il giocatore assente
            absent_games_raw = await dunkest.get_player_games(absent_player_id)
            absent_played_dates: set[str] = {
                g.get("gameDate", "")[:10]
                for g in absent_games_raw
                if (g.get("min") or 0) >= 5
            }
            games_without = [
                g for g in games
                if (g.get("min") or 0) >= 10
                and g.get("gameDate", "")[:10] not in absent_played_dates
            ]
            impact = dunkest.compute_teammate_impact(games, games_without)
            impact["games_without"] = games_without

            # ── [1] Usage redistribution boost ───────────────────────────────
            # Stima quanto usage assorbirà questo compagno dall'assente.
            # Se il boost stimato supera le stats normali → rafforza la tesi.
            teammate_games_map = {player_name: games}
            usage_redistrib = dunkest.compute_usage_redistribution(
                absent_games=absent_games_raw,
                teammate_games_map=teammate_games_map,
            )
            if usage_redistrib.get(player_name):
                impact["usage_boost"] = usage_redistrib[player_name]
                logger.debug(
                    "Usage redistribution per %s: %s",
                    player_name, usage_redistrib[player_name],
                )

        # ── Matchup difensivo ─────────────────────────────────────────────
        position = get_player_position(player_name)
        matchup = matchup_client.classify_matchup(opposing_team, position, defense_data)
        matchup_modifier = matchup_client.matchup_ev_modifier(matchup["rating"])

        # ── Analisi per ogni prop disponibile ────────────────────────────
        prop_analyses: list[dict] = []
        player_opps = 0

        for (market, direction, line), bk_odds in props.items():
            analysis = await _analyse_single_prop(
                player_name=player_name,
                player_id=player_id,
                market=market,
                direction=direction,
                line=line,
                bk_odds=bk_odds,
                games=games,
                impact=impact,
                matchup_modifier=matchup_modifier,
                dunkest=dunkest,
                db=db,
                match=match,
            )
            if analysis:
                prop_analyses.append(analysis)
                if analysis.get("opportunity_created"):
                    player_opps += 1

        opps_found += player_opps

        # ── Full stats overview (anche senza quote correnti) ──────────────
        recent_avgs = dunkest.get_recent_averages(games, last_n=10)
        absent_avgs = {}
        if impact and impact.get("significant"):
            absent_avgs = impact.get("avg_without", {})

        teammate_report = {
            "player_name":     player_name,
            "position":        position,
            "matchup_rating":  matchup["rating"],
            "matchup_reason":  matchup["reasoning"],
            "recent_avg":      recent_avgs,
            "absent_avg":      absent_avgs,
            "teammate_verdict": impact.get("verdict") if impact else None,
            "n_games_without": impact.get("n_without") if impact else 0,
            "usage_boost":     impact.get("usage_boost") if impact else None,
            "props_with_ev":   [p for p in prop_analyses if p.get("ev", 0) >= MIN_EV_ABSENCE],
            "opportunities":   player_opps,
        }
        teammate_reports.append(teammate_report)

    # ── 7. Salva e notifica ─────────────────────────────────────────────────
    if opps_found:
        await db.commit()

    await _send_absence_alert(
        absent_player=absent_player_name,
        absent_team=absent_team,
        match=match,
        opposing_team=opposing_team,
        teammate_reports=teammate_reports,
        total_opps=opps_found,
    )

    logger.info(
        "ABSENCE PIPELINE done: %s OUT → %d compagni analizzati, %d opportunità",
        absent_player_name, len(teammate_reports), opps_found,
    )

    return {
        "absent_player":       absent_player_name,
        "team":                absent_team,
        "match":               match.display_name() if match else None,
        "teammates_analysed":  len(teammate_reports),
        "opportunities_found": opps_found,
        "teammate_reports":    teammate_reports,
    }


async def _find_tonight_match(
    team_name: str,
    db: AsyncSession,
    now: datetime,
    window_end: datetime,
) -> Match | None:
    """Cerca la partita di stasera che coinvolge il team indicato."""
    result = await db.execute(
        select(Match)
        .where(Match.sport == "basketball")
        .where(Match.match_date >= now - timedelta(hours=1))
        .where(Match.match_date <= window_end)
        .where(Match.status.in_(["scheduled", "pending", "upcoming"]))
        .limit(50)
    )
    matches: list[Match] = result.scalars().all()

    team_lower = team_name.lower()
    for m in matches:
        home = (m.home_team or "").lower()
        away = (m.away_team or "").lower()
        # Cerca parole del nome squadra (es. "lakers" in "los angeles lakers")
        team_words = [w for w in team_lower.split() if len(w) > 3]
        if any(w in home or w in away for w in team_words):
            return m

    return None


def _get_opposing_team(match: Match, our_team: str) -> str:
    """Ritorna la squadra avversaria nella partita."""
    our_lower = our_team.lower()
    home = (match.home_team or "").lower()
    away = (match.away_team or "").lower()
    our_words = [w for w in our_lower.split() if len(w) > 3]
    if any(w in home for w in our_words):
        return match.away_team or ""
    return match.home_team or ""


async def _analyse_single_prop(
    player_name: str,
    player_id: int,
    market: str,
    direction: str,
    line: float,
    bk_odds: dict[str, float],
    games: list[dict],
    impact: dict | None,
    matchup_modifier: float,
    dunkest: DunkestClient,
    db: AsyncSession,
    match: Match,
) -> dict | None:
    """
    Analisi EV per un singolo prop, tenendo conto dell'assenza.
    """
    soft_odds = [(bk, odds) for bk, odds in bk_odds.items() if bk not in SHARP_PROP_BOOKMAKERS]
    if not soft_odds:
        return None

    best_bk, best_odds = max(soft_odds, key=lambda x: x[1])
    if best_odds < MIN_ODDS or best_odds > MAX_ODDS:
        return None

    pinnacle_odds = next((bk_odds[bk] for bk in SHARP_PROP_BOOKMAKERS if bk in bk_odds), None)
    pinnacle_prob  = (1.0 / pinnacle_odds) if pinnacle_odds else None

    # Calcola hit rate (normale)
    if market in COMBO_MARKETS:
        combo_keys = COMBO_MARKETS[market]
        stat_key = None
    else:
        stat_key = MARKET_TO_STAT.get(market)
        combo_keys = None

    normal_hr, normal_n = 0.5, 0
    if stat_key or combo_keys:
        normal_hr, normal_n = dunkest.compute_hit_rate(
            games=games,
            stat_key=stat_key or "",
            line=line,
            direction=direction,
            last_n=20,
            combo_keys=combo_keys,
        )

    # Hit rate nelle partite senza il compagno assente
    absence_hr, absence_n = normal_hr, normal_n
    if impact and impact.get("significant") and impact.get("games_without"):
        games_wo = impact["games_without"]
        if len(games_wo) >= MIN_SAMPLE_ABSENCE and (stat_key or combo_keys):
            absence_hr, absence_n = dunkest.compute_hit_rate(
                games=games_wo,
                stat_key=stat_key or "",
                line=line,
                direction=direction,
                last_n=20,
                combo_keys=combo_keys,
            )

    # Scegli la prob migliore per il calcolo EV
    if absence_n >= MIN_SAMPLE_ABSENCE:
        final_prob = absence_hr
        source_prob = "absence_hit_rate"
    elif normal_n >= MIN_SAMPLE_SIZE:
        final_prob = normal_hr
        source_prob = "normal_hit_rate"
    elif pinnacle_prob:
        final_prob = pinnacle_prob
        source_prob = "pinnacle"
    else:
        return None

    # Applica matchup modifier sulla probabilità
    adjusted_prob = min(0.98, final_prob * matchup_modifier)
    ev = (adjusted_prob * best_odds) - 1.0

    # Filtri qualità
    if ev < MIN_EV_ABSENCE:
        return {"market": market, "direction": direction, "line": line,
                "ev": ev, "hit_rate": final_prob, "opportunity_created": False}

    if final_prob < MIN_HIT_ABSENCE:
        return {"market": market, "direction": direction, "line": line,
                "ev": ev, "hit_rate": final_prob, "opportunity_created": False}

    # Anti-duplicati
    outcome_label = f"{player_name} — {direction.capitalize()} {line}"
    existing = await db.scalar(
        select(func.count(BettingOpportunity.id)).where(
            BettingOpportunity.match_id == match.id,
            BettingOpportunity.market == market,
            BettingOpportunity.outcome == outcome_label,
            BettingOpportunity.status.in_(["pending", "in_attesa", "bet_placed"]),
        )
    )
    if existing:
        return {"market": market, "direction": direction, "line": line,
                "ev": ev, "hit_rate": final_prob, "opportunity_created": False}

    # Crea opportunità
    opportunity = BettingOpportunity(
        match_id=match.id,
        market=market,
        outcome=outcome_label,
        bookmaker=best_bk,
        best_odds=best_odds,
        model_probability=adjusted_prob,
        consensus_votes={
            "source": "absence_pipeline",
            "player": player_name,
            "stat": stat_key or market,
            "line": line,
            "direction": direction,
            "normal_hit_rate": round(normal_hr, 3),
            "absence_hit_rate": round(absence_hr, 3) if absence_n >= MIN_SAMPLE_ABSENCE else None,
            "n_absence_games": absence_n,
            "prob_source": source_prob,
            "pinnacle_prob": round(pinnacle_prob, 4) if pinnacle_prob else None,
            "ev": round(ev, 4),
        },
        uncertainty_score=0.30,
        expected_value=ev,
        tier="A" if ev >= 0.09 else "B",
        edge=round(ev * 100, 2),
        bet_type="singola",
        confidence_level="medium",
        status="pending",
        reference_source="absence_dunkest",
        expires_at=match.match_date,
    )
    db.add(opportunity)
    await db.flush()

    logger.info(
        "ABSENCE EV: %s %s %.1f @ %.2f | EV %+.1f%% (hr_norm=%.0f%% hr_abs=%.0f%% n=%d)",
        player_name, direction.upper(), line, best_odds,
        ev * 100, normal_hr * 100, absence_hr * 100, absence_n,
    )

    return {
        "market":              market,
        "direction":           direction,
        "line":                line,
        "best_odds":           best_odds,
        "bookmaker":           best_bk,
        "ev":                  ev,
        "hit_rate":            final_prob,
        "absence_hit_rate":    absence_hr,
        "n_absence_games":     absence_n,
        "opportunity_created": True,
        "opportunity_id":      str(opportunity.id)[:8],
    }


async def _send_absence_alert(
    absent_player: str,
    absent_team: str,
    match: Match,
    opposing_team: str,
    teammate_reports: list[dict],
    total_opps: int,
) -> None:
    """
    Invia Telegram alert con panoramica completa dell'impatto dell'assenza.
    """
    try:
        from app.services.telegram_service import _send

        ev_reports = [r for r in teammate_reports if r.get("props_with_ev")]
        exploitable = [r for r in teammate_reports if r.get("matchup_rating") == "exploitable"]

        header = (
            f"🚨 <b>NBA ABSENCE ALERT</b>\n"
            f"🏀 {match.display_name()}\n\n"
            f"❌ <b>{absent_player}</b> ({absent_team}) — CONFERMATO OUT\n"
            f"📊 Analisi impatto su {len(teammate_reports)} compagni\n"
        )

        # Matchup info
        if opposing_team:
            header += f"🆚 Avversario: {opposing_team}\n"

        lines: list[str] = [header]

        # Report per ogni compagno con opportunità
        for report in sorted(ev_reports, key=lambda r: len(r.get("props_with_ev", [])), reverse=True)[:5]:
            name     = report["player_name"]
            verdict  = report.get("teammate_verdict", "")
            mrating  = report.get("matchup_rating", "neutral")
            n_wo     = report.get("n_games_without", 0)
            avg_norm = report.get("recent_avg", {})
            avg_abs  = report.get("absent_avg", {})

            # Emoji contesto
            v_emoji = {"better_without": "📈", "worse_without": "📉", "no_difference": "↔️"}.get(verdict, "")
            m_emoji = {"exploitable": "🟢", "neutral": "⚪", "tough": "🔴"}.get(mrating, "")

            teammate_block = f"\n👤 <b>{name}</b> {v_emoji} {m_emoji}\n"

            # Stats with/without
            if avg_abs and avg_norm:
                pts_norm = avg_norm.get("pts", 0)
                pts_abs  = avg_abs.get("pts", 0)
                reb_norm = avg_norm.get("reb", 0)
                reb_abs  = avg_abs.get("reb", 0)
                ast_norm = avg_norm.get("ast", 0)
                ast_abs  = avg_abs.get("ast", 0)
                teammate_block += (
                    f"  Senza {absent_player} ({n_wo} gare):\n"
                    f"  PTS: {pts_norm:.1f} → <b>{pts_abs:.1f}</b> "
                    f"({pts_abs-pts_norm:+.1f})\n"
                    f"  REB: {reb_norm:.1f} → <b>{reb_abs:.1f}</b> "
                    f"({reb_abs-reb_norm:+.1f})\n"
                    f"  AST: {ast_norm:.1f} → <b>{ast_abs:.1f}</b> "
                    f"({ast_abs-ast_norm:+.1f})\n"
                )
                # Usage redistribution boost
                usage_boost = report.get("usage_boost") or {}
                pts_boost = usage_boost.get("pts_boost", 0)
                min_boost = usage_boost.get("min_boost", 0)
                if pts_boost >= 1.0:
                    teammate_block += (
                        f"  📈 Usage boost stimato: <b>+{pts_boost:.1f} pts</b> "
                        f"/ +{min_boost:.0f} min\n"
                    )

            # Props con EV
            for prop in report.get("props_with_ev", [])[:3]:
                ev_pct = prop.get("ev", 0) * 100
                teammate_block += (
                    f"  ✅ {prop['direction'].upper()} {prop['line']} "
                    f"@ {prop.get('best_odds', 0):.2f} | EV {ev_pct:+.1f}%\n"
                )

            lines.append(teammate_block)

        # Matchup exploitable summary
        if exploitable:
            exp_names = ", ".join(r["player_name"].split()[1] for r in exploitable[:3])
            lines.append(f"\n🟢 Matchup favorevole vs {opposing_team}: {exp_names}")

        if total_opps:
            lines.append(f"\n💰 <b>{total_opps} opportunità generate</b>")

        msg = "".join(lines)
        await _send(msg)

    except Exception as exc:
        logger.warning("Absence pipeline Telegram alert failed: %s", exc)


# ── Entry point per analisi di tutti i giocatori della squadra ────────────────

async def analyse_all_teammates_stats(
    team_name: str,
    opposing_team: str,
    db: AsyncSession,
    match: Match,
) -> list[dict]:
    """
    Calcola stats complete (pts/reb/ast/3pm/blk/stl) per tutti i giocatori
    di una squadra che hanno props disponibili, includendo matchup difensivo.

    Utile per avere un panorama completo anche senza assenze specifiche.
    """
    now = datetime.now(timezone.utc)
    freshness = now.replace(hour=0, minute=0, second=0, microsecond=0)

    props_result = await db.execute(
        select(MatchOdds)
        .where(MatchOdds.match_id == match.id)
        .where(MatchOdds.market.in_(PLAYER_PROP_MARKETS))
        .where(MatchOdds.fetched_at >= freshness)
        .limit(500)
    )
    all_props: list[MatchOdds] = props_result.scalars().all()

    # Raggruppa per player
    player_names: set[str] = set()
    for row in all_props:
        from app.services.dunkest_client import parse_player_prop_outcome
        pname, _, _ = parse_player_prop_outcome(row.outcome, row.market)
        if pname:
            player_names.add(pname)

    matchup_client = NBAMatchupClient()
    defense_data = await matchup_client.get_league_defense_rankings()
    dunkest = DunkestClient()

    reports: list[dict] = []
    for player_name in player_names:
        player_id = find_player_id(player_name)
        if not player_id:
            continue

        games = await dunkest.get_player_games(player_id)
        if not games:
            continue

        position = get_player_position(player_name)
        matchup  = matchup_client.classify_matchup(opposing_team, position, defense_data)
        avgs     = dunkest.get_recent_averages(games, last_n=10)

        # Hit rates su linee tipiche (median)
        hit_rates: dict[str, float] = {}
        for stat_key in ALL_STAT_KEYS:
            avg_val = avgs.get(stat_key, 0)
            if avg_val > 0:
                line = round(avg_val - 0.5, 1)  # linea leggermente sotto la media
                hr, _ = dunkest.compute_hit_rate(games, stat_key, line, "over", last_n=20)
                hit_rates[stat_key] = hr

        reports.append({
            "player_name":    player_name,
            "position":       position,
            "matchup":        matchup["rating"],
            "matchup_detail": matchup["reasoning"],
            "recent_avg_10":  avgs,
            "hit_rates":      hit_rates,
        })

    return reports
