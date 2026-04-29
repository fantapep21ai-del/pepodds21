"""
Pipeline di analisi — architettura Pinnacle-first con consensus multi-agente.

Flusso per ogni partita:
  1. Skip partite già iniziate
  2. Calcolo no-vig matematico su quote Pinnacle/Betfair (0 LLM)
  3. Confronto con bookmaker soft → trova EV > 3%
  4. Tutti gli agenti in parallelo (LLM calls condizionali ai dati disponibili):
       StatsAgent, OddsAgent, FormAgent, H2HAgent, InjuryAgent, NewsAgent,
       WeatherAgent → segnale di accordo/disaccordo con Pinnacle
       UncertaintyAgent → gate qualitativo (blocca se score ≥ 0.70)
  5. Blocco agenti: se ≥2 specialist contraddicono Pinnacle → skip
  6. Classifica per tipo: singola / scalata / combinata
  7. Calcola affidabilità modulata dal segnale agenti + ELO + timing
  8. Invia alert Telegram con bottoni — l'utente decide l'importo

Costo AI: ~€1.5-2.5/mese (haiku + prompt caching + conditional execution).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from sqlalchemy import select, func, insert, and_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.match import Match, MatchOdds
from app.db.models.agent import AgentRun, AgentVote, AgentScore
from app.db.models.opportunity import BettingOpportunity
from app.agents.base import AgentResult
from app.agents.agents import (
    StatsAgent, OddsAgent, FormAgent, H2HAgent,
    InjuryAgent, NewsAgent, WeatherAgent, UncertaintyAgent,
)
from app.agents.consensus import compute_no_vig, find_value_opportunities, compute_reliability
from app.agents.tiers import classify as classify_tier
from app.agents.synthesis import synthesize_agent_results

logger = logging.getLogger(__name__)

UNCERTAINTY_GATE    = 0.70   # UncertaintyAgent: blocca se score ≥ 0.70
AGENT_BLOCK_SIGNAL  = 0.30   # Blocca se segnale agenti < 0.30 con ≥2 agenti disponibili
SHARP_BOOKMAKERS    = {"pinnacle", "betfair"}  # Reference markets for no-vig calculation

# ── Range quote & EV threshold ────────────────────────────────────────────────
ODDS_MIN          = 1.4    # sotto: escluded interamente
ODDS_MAX          = 5.00   # sopra: outsider estremo


def get_ev_threshold(odds: float) -> float | None:
    """
    Dynamic EV threshold based on odds range.

    - Odds < 1.4: EXCLUDE (return None)
    - Odds 1.4–3.0: min_ev = 3.5% (0.035)
    - Odds > 3.0: min_ev = 8.0% (0.08)
    """
    if odds < ODDS_MIN:
        return None  # Exclude
    elif odds <= 3.0:
        return 0.035  # 3.5%
    else:
        return 0.08   # 8.0%


async def analyse_match(match: Match, db: AsyncSession) -> int:
    """
    Pipeline completo per una partita.
    Ritorna numero di opportunità trovate e salvate.
    """
    logger.info("Pipeline: analysing match %s", match.display_name())

    # ── 0. Skip partite già iniziate ─────────────────────────────────────────
    now = datetime.now(timezone.utc)
    if match.match_date and match.match_date < now - timedelta(minutes=30):
        logger.info("Skipping %s — match already started", match.display_name())
        return 0

    # ── 1. Skip senza quote ───────────────────────────────────────────────────
    odds_count = await db.scalar(
        select(func.count(MatchOdds.id)).where(MatchOdds.match_id == match.id)
    )
    if not odds_count:
        return 0

    # ── 2. Carica e deduplica quote (solo quote fresche, ≤ 6 ore) ─────────────
    freshness_cutoff = now - timedelta(hours=6)
    odds_result = await db.execute(
        select(MatchOdds)
        .where(MatchOdds.match_id == match.id)
        .where(MatchOdds.fetched_at >= freshness_cutoff)
        .order_by(MatchOdds.fetched_at.desc())
        .limit(300)
    )
    seen: set[tuple] = set()
    all_odds: list[dict] = []
    for o in odds_result.scalars().all():
        key = (o.bookmaker, o.market, o.outcome)
        if key not in seen:
            seen.add(key)
            all_odds.append({
                "bookmaker": o.bookmaker,
                "market": o.market,
                "outcome": o.outcome,
                "odds": float(o.odds),
            })

    # Filtra totals senza linea esplicita (es. "Over" generico — linea non confrontabile)
    import re
    def _has_line(outcome: str) -> bool:
        return bool(re.search(r'\d', outcome))

    all_odds = [
        o for o in all_odds
        if o["market"] != "totals" or _has_line(o["outcome"])
    ]

    sharp_odds = [o for o in all_odds if o["bookmaker"] in SHARP_BOOKMAKERS]

    # Filtra bookmaker blacklistati dal sistema CLV
    clv_blacklist = await _get_clv_blacklist()
    soft_odds = [
        o for o in all_odds
        if o["bookmaker"] not in SHARP_BOOKMAKERS
        and o["bookmaker"] not in clv_blacklist
    ]
    if clv_blacklist:
        logger.debug("CLV blacklist attiva: %s", clv_blacklist)

    if not sharp_odds:
        if not all_odds:
            logger.info("No fresh odds (< 6h) for %s — skip", match.display_name())
            # Update match analysis status: no data
            await db.execute(
                update(Match)
                .where(Match.id == match.id)
                .values(
                    analysis_status="no_data",
                    analysis_reason={"type": "no_data", "reasons": ["no_fresh_odds"]},
                    updated_at=datetime.now(timezone.utc)
                )
            )
            await db.commit()
        else:
            logger.info("No sharp odds for %s — skip", match.display_name())
            await db.execute(
                update(Match)
                .where(Match.id == match.id)
                .values(
                    analysis_status="no_data",
                    analysis_reason={"type": "no_data", "reasons": ["no_pinnacle_quotes"]},
                    updated_at=datetime.now(timezone.utc)
                )
            )
            await db.commit()
        return 0

    # ── 3. No-vig + EV (matematica pura, 0 LLM) ──────────────────────────────
    pinnacle_probs = compute_no_vig(sharp_odds)
    if not pinnacle_probs:
        await db.execute(
            update(Match)
            .where(Match.id == match.id)
            .values(
                analysis_status="incomplete",
                analysis_reason={"type": "incomplete", "reasons": ["no_vig_calculation_failed"]},
                updated_at=datetime.now(timezone.utc)
            )
        )
        await db.commit()
        return 0

    value_candidates = find_value_opportunities(pinnacle_probs, soft_odds, min_ev=0.035)

    # Apply dynamic EV threshold based on odds range
    filtered_candidates = []
    for opp in value_candidates:
        threshold = get_ev_threshold(opp["best_odds"])
        if threshold is not None and opp["expected_value"] >= threshold:
            filtered_candidates.append(opp)

    value_candidates = filtered_candidates

    # ── 2.5. Assess data completeness ─────────────────────────────────────────
    # Evaluate what data was available for analysis before running agents
    analysis_status, missing_data = assess_analysis_completeness(
        match=match,
        all_odds=all_odds,
        sharp_odds=sharp_odds,
    )

    if not value_candidates:
        logger.info("No value found for %s (Pinnacle math)", match.display_name())
        # Update analysis status: complete but no value
        await db.execute(
            update(Match)
            .where(Match.id == match.id)
            .values(
                analysis_status=analysis_status,
                analysis_reason={"type": analysis_status, "reasons": missing_data} if missing_data else None,
                updated_at=datetime.now(timezone.utc)
            )
        )
        await db.commit()
        return 0

    logger.info(
        "Value candidates for %s: %d (best EV: %+.1f%%)",
        match.display_name(),
        len(value_candidates),
        max(c["ev"] for c in value_candidates) * 100,
    )

    # ── 4. Tutti gli agenti in parallelo ─────────────────────────────────────
    # Carica pesi storici (Brier score) — agenti più calibrati pesano di più
    agent_weights = await _load_agent_weights(db)

    ctx = await _build_context(match, db, all_odds)
    specialist_results, uncertainty_result = await _run_agents_parallel(match, ctx)

    # Persisti tutti i risultati in agent_runs
    await _persist_agent_result(match, uncertainty_result, db)
    for res in specialist_results.values():
        await _persist_agent_result(match, res, db)

    uncertainty_score = _extract_uncertainty_score(uncertainty_result)
    uncertainty_reasoning = _extract_uncertainty_reasoning(uncertainty_result)

    if uncertainty_score >= UNCERTAINTY_GATE:
        logger.info(
            "Uncertainty gate for %s: %.2f >= %.2f — skipping (%s)",
            match.display_name(), uncertainty_score, UNCERTAINTY_GATE, uncertainty_reasoning,
        )
        return 0

    # ── 5. Classifica e persisti ──────────────────────────────────────────────
    persisted = 0

    for candidate in value_candidates:
        market        = candidate["market"]
        outcome       = candidate["outcome"]
        bookmaker     = candidate["bookmaker"]
        best_odds_val = candidate["best_odds"]
        no_vig_prob   = candidate["no_vig_prob"]
        ev            = candidate["ev"]
        n_confirming  = candidate["n_confirming"]

        # ── Filtro range quote ────────────────────────────────────────────────
        if best_odds_val < ODDS_MIN or best_odds_val > ODDS_MAX:
            logger.info("Skipping %s %s @ %.2f — fuori range", market, outcome, best_odds_val)
            continue

        # ── Filtro EV per range odds: quote alte richiedono EV superiore ──────
        # Quote 1.40-3.00: EV >= 3.5%
        # Quote > 3.00: EV >= 8%
        if best_odds_val > 3.00:
            if ev < 0.08:
                logger.info(
                    "Skipping %s %s @ %.2f — quota > 3.00 richiede EV >= 8%% (attuale: %+.1f%%)",
                    market, outcome, best_odds_val, ev * 100
                )
                continue
        elif ev < 0.035:
            logger.info(
                "Skipping %s %s @ %.2f — EV insufficiente (%.1f%% < 3.5%%)",
                market, outcome, best_odds_val, ev * 100
            )
            continue

        # ── Classificazione tier e bet_type ──────────────────────────────────
        tier_result = classify_tier(
            expected_value=ev,
            uncertainty_score=uncertainty_score,
            model_probability=no_vig_prob,
            best_odds=best_odds_val,
        )

        if tier_result.tier == "C":
            continue

        # ── Segnale agenti specialist (pesato per performance storica) ──────────
        agent_signal, n_agents_agreeing = _compute_agent_signal(
            specialist_results, market, outcome, no_vig_prob, agent_weights
        )
        # Raccoglie il reasoning degli agenti per l'alert Telegram
        agent_insights = _collect_agent_insights(specialist_results, market, outcome)

        # Blocco per forte disaccordo agenti (richiede ≥2 agenti con dati sufficienti)
        if len(specialist_results) >= 2 and agent_signal < AGENT_BLOCK_SIGNAL:
            logger.info(
                "Skipping %s %s @ %.2f — forte disaccordo agenti (signal=%.2f < %.2f)",
                market, outcome, best_odds_val, agent_signal, AGENT_BLOCK_SIGNAL,
            )
            continue

        # ── ELO agreement (solo calcio, h2h) ─────────────────────────────────
        elo_data = (match.raw_stats or {}).get("elo", {})
        elo_agreement = _compute_elo_agreement(market, outcome, match, elo_data, no_vig_prob)

        # ── Market timing score ───────────────────────────────────────────────
        timing_modifier = _compute_timing_modifier(match.match_date)

        # ── Affidabilità base ─────────────────────────────────────────────────
        raw_reliability = compute_reliability(
            ev=ev,
            uncertainty_score=uncertainty_score,
            n_confirming=n_confirming,
            reference_source="pinnacle_no_vig",
            elo_agreement=elo_agreement,
            timing_modifier=timing_modifier,
        )

        # Modulatori: contesto qualitativo (uncertainty) + segnale agenti
        context_mult = _context_reliability_multiplier(uncertainty_score)
        agent_mult   = _agent_signal_multiplier(agent_signal)
        reliability  = max(0.05, min(0.92, raw_reliability * context_mult * agent_mult))

        # ── Filtri affidabilità per tipo di giocata ──────────────────────────
        if best_odds_val > 2.30 and tier_result.bet_type == "singola" and reliability < 0.70:
            logger.info(
                "Skipping %s %s @ %.2f — quota alta con affidabilità insufficiente (%.0f%% < 70%%)",
                market, outcome, best_odds_val, reliability * 100,
            )
            continue

        # ── Anti-duplicati ────────────────────────────────────────────────────
        existing = await db.scalar(
            select(func.count(BettingOpportunity.id)).where(
                BettingOpportunity.match_id == match.id,
                BettingOpportunity.market == market,
                BettingOpportunity.outcome == outcome,
                BettingOpportunity.status.in_(["pending", "in_attesa", "bet_placed"]),
            )
        )
        if existing:
            logger.info(
                "Skip duplicato %s %s @ %.2f — opportunità già esistente",
                market, outcome, best_odds_val,
            )
            continue

        logger.info(
            "Opportunità: %s %s @ %.2f — EV %+.1f%% — tipo:%s — affidabilità:%.0f%% — "
            "agenti:%d signal:%.2f",
            market, outcome, best_odds_val, ev * 100,
            tier_result.bet_type, reliability * 100,
            len(specialist_results), agent_signal,
        )

        # ── SynthesisAgent: integra i specialist in una narrativa coerente ────────
        synthesis_data = await synthesize_agent_results(
            match_name=match.display_name(),
            competition=(match.competition.name if match.competition else "N/A"),
            sport=match.sport or "football",
            specialist_results=specialist_results,
            uncertainty_score=uncertainty_score,
            no_vig_prob=no_vig_prob,
            best_odds=best_odds_val,
            expected_value=ev,
        )

        # Prepara consensus_votes con segnali individuali degli specialist
        specialist_signals = {
            agent_name: {
                "signal": round(res.get("signal", 0.5), 3),
                "confidence": round(res.get("confidence", 0.5), 3),
                "reasoning": res.get("reasoning", "")[:200],  # trancate per spazio
            }
            for agent_name, res in specialist_results.items()
        }

        consensus_votes = {
            "pinnacle_no_vig":          round(no_vig_prob, 4),
            "ev":                       round(ev, 4),
            "n_confirming_bookmakers":  n_confirming,
            "reliability":              round(reliability, 4),
            "agent_signal":             round(agent_signal, 3),
            "n_agents_agreeing":        n_agents_agreeing,
            "agents_run":               sorted(specialist_results.keys()),
            # NUOVO: segnali individuali degli specialist
            "specialist_signals":       specialist_signals,
            # NUOVO: sintesi narrativa
            "synthesis": synthesis_data,
        }

        # ── [BUG #1 FIX] Idempotent insert per evitare race condition ──────────
        opp_values = {
            "id": uuid4(),
            "match_id": match.id,
            "market": market,
            "outcome": outcome,
            "bookmaker": bookmaker,
            "best_odds": best_odds_val,
            "model_probability": no_vig_prob,
            "consensus_votes": consensus_votes,
            "uncertainty_score": uncertainty_score,
            "expected_value": ev,
            "tier": tier_result.tier,
            "edge": tier_result.edge,
            "bet_type": tier_result.bet_type,
            "confidence_level": tier_result.confidence_level,
            "status": "pending",
            "rejection_reason": None,
            "uncertainty_blocked": False,
            "reference_source": "pinnacle_no_vig",
            "expires_at": match.match_date or (now + timedelta(days=7)),
        }

        stmt = (
            insert(BettingOpportunity)
            .values(**opp_values)
            .on_conflict_do_nothing(
                index_elements=['match_id', 'market', 'outcome']
            )
            .returning(BettingOpportunity.id)
        )
        result = await db.execute(stmt)
        inserted_id = result.scalar_one_or_none()
        is_new = inserted_id is not None

        if is_new:
            opportunity = await db.scalar(
                select(BettingOpportunity).where(
                    BettingOpportunity.id == inserted_id
                )
            )
        else:
            opportunity = await db.scalar(
                select(BettingOpportunity)
                .where(
                    and_(
                        BettingOpportunity.match_id == match.id,
                        BettingOpportunity.market == market,
                        BettingOpportunity.outcome == outcome,
                    )
                )
                .order_by(BettingOpportunity.created_at.desc())
                .limit(1)
            )

        if not opportunity:
            logger.error(
                "Opportunity retrieval failed after insert/conflict: %s %s %s",
                match.id, market, outcome
            )
            continue

        if is_new:
            persisted += 1
            logger.info(
                "✓ Opportunità CREATA: %s %s @ %.2f — EV %+.1f%% — affidabilità:%.0f%%",
                market, outcome, best_odds_val, ev * 100, reliability * 100,
            )
        else:
            logger.info(
                "⊙ Opportunità ESISTENTE: %s %s @ %.2f — skip (già registrata)",
                market, outcome, best_odds_val,
            )

        # Passa synthesis data all'alert per miglior formatting
        synthesis_narrative = synthesis_data.get("narrative", "")
        await _send_alert(opportunity, match, reliability, uncertainty_reasoning, agent_insights, synthesis_narrative)

    # Mark match as fully analyzed (complete)
    await db.execute(
        update(Match)
        .where(Match.id == match.id)
        .values(
            analysis_status="complete",
            analysis_reason=None,
            updated_at=datetime.now(timezone.utc)
        )
    )

    await db.commit()
    logger.info(
        "Pipeline done for %s — %d opportunities persisted (agents: %s)",
        match.display_name(), persisted, ", ".join(specialist_results.keys()) or "none",
    )
    return persisted


# ── Esecuzione agenti in parallelo ────────────────────────────────────────────

async def _run_agents_parallel(
    match: Match,
    ctx: dict,
) -> tuple[dict[str, AgentResult], AgentResult]:
    """
    Esegue tutti gli agenti disponibili in parallelo con asyncio.gather.
    Gli agenti specialist girano solo se i dati rilevanti sono presenti nel contesto.
    UncertaintyAgent gira sempre come gate finale.

    Ritorna (specialist_results, uncertainty_result).
    Costo tipico: $0.001-0.005 per partita (haiku + prompt caching).
    """
    tasks: list[tuple[str, object]] = []

    # Specialist — condizionali ai dati disponibili
    if ctx.get("stats"):
        tasks.append(("stats", StatsAgent().run(ctx)))
    if len(ctx.get("odds") or []) >= 4:  # almeno 4 bookmaker per analisi mercato
        tasks.append(("odds", OddsAgent().run(ctx)))
    if ctx.get("form_stats"):
        tasks.append(("form", FormAgent().run(ctx)))
    if ctx.get("h2h"):
        tasks.append(("h2h", H2HAgent().run(ctx)))
    if ctx.get("injuries"):
        tasks.append(("injury", InjuryAgent().run(ctx)))
    if ctx.get("news_summary"):
        tasks.append(("news", NewsAgent().run(ctx)))
    if match.sport == "football" and ctx.get("weather"):
        tasks.append(("weather", WeatherAgent().run(ctx)))

    # UncertaintyAgent — sempre
    tasks.append(("uncertainty", UncertaintyAgent().run(ctx)))

    names  = [n for n, _ in tasks]
    coros  = [c for _, c in tasks]
    raw    = await asyncio.gather(*coros, return_exceptions=True)

    specialist: dict[str, AgentResult] = {}
    uncertainty_result: AgentResult | None = None

    for name, res in zip(names, raw):
        if isinstance(res, Exception):
            logger.warning("Agent %s raised exception: %s", name, res)
            continue
        if name == "uncertainty":
            uncertainty_result = res
        else:
            specialist[name] = res

    if uncertainty_result is None:
        uncertainty_result = AgentResult(
            agent_name="uncertainty",
            estimates=[],
            error="agent exception — fallback a score neutro",
        )

    logger.info(
        "Agents completati per %s: %d specialist [%s] + uncertainty (score=%.2f)",
        ctx.get("match_name", "?"),
        len(specialist),
        ", ".join(specialist.keys()) or "nessuno",
        _extract_uncertainty_score(uncertainty_result),
    )
    return specialist, uncertainty_result


# ── Segnale di consensus degli agenti ─────────────────────────────────────────

def _compute_agent_signal(
    specialist_results: dict[str, AgentResult],
    market: str,
    outcome: str,
    pinnacle_prob: float,
    agent_weights: dict[str, float] | None = None,
) -> tuple[float, int]:
    """
    Calcola il segnale di accordo tra agenti specialist e probabilità Pinnacle no-vig.
    Usa i pesi storici (Brier score) per dare più importanza agli agenti più calibrati.

    Ritorna (signal_score: float 0-1, n_agreeing: int).
      > 0.65 → agenti confermano il value (boost affidabilità +10%)
      0.45-0.65 → neutro
      0.30-0.45 → dubbio (penalità -15%)
      < 0.30 → blocco se ≥2 agenti con dati sufficienti

    La differenza con n_confirming_bookmakers:
      n_confirming = quanti bookmaker soft offrono quota con EV > 0 (lato mercato)
      agent_signal = quanto gli analisti AI concordano con la prob Pinnacle (lato analisi)
    """
    weights = agent_weights or {}
    estimates: list[tuple[float, float]] = []  # (probability, weight_effettivo)

    for name, result in specialist_results.items():
        if result.failed:
            continue
        historical_weight = weights.get(name, 1.0)  # 1.0 finché non ci sono dati storici
        for est in result.estimates:
            if est.get("market") == market and est.get("outcome") == outcome:
                prob = float(est.get("probability", 0.5))
                conf = float(est.get("confidence", 0.5))
                if conf >= 0.30:
                    # Peso combinato: confidenza dell'agente × performance storica
                    combined_weight = conf * historical_weight
                    estimates.append((prob, combined_weight))
                break  # un solo estimate per agente per questa (market, outcome)

    if not estimates:
        return 0.5, 0  # nessun dato → neutro

    total_weight = sum(c for _, c in estimates)
    weighted_avg = sum(p * c for p, c in estimates) / total_weight

    # Gap tra probabilità media agenti e Pinnacle no-vig
    # Scala: gap ±0.20 → signal 0.0 o 1.0
    gap    = weighted_avg - pinnacle_prob
    signal = max(0.0, min(1.0, 0.5 + gap / 0.20))

    # Quanti agenti confermano (prob > 85% della prob Pinnacle)
    n_agreeing = sum(1 for p, _ in estimates if p >= pinnacle_prob * 0.85)

    logger.debug(
        "Agent signal [%s %s]: pinnacle=%.3f agents_avg=%.3f gap=%.3f signal=%.2f (%d/%d agreeing)",
        market, outcome, pinnacle_prob, weighted_avg, gap, signal, n_agreeing, len(estimates),
    )
    return signal, n_agreeing


async def _load_agent_weights(db: AsyncSession) -> dict[str, float]:
    """
    Carica i pesi degli agenti basati sulle performance storiche (Brier score).
    Agenti più calibrati nel tempo ricevono peso più alto nel signal computation.
    Ritorna dict vuoto se non ci sono ancora dati (tutto peso 1.0 di default).
    """
    try:
        result = await db.execute(select(AgentScore))
        return {s.agent_name: float(s.weight) for s in result.scalars().all()}
    except Exception:
        return {}


def _collect_agent_insights(
    specialist_results: dict[str, AgentResult],
    market: str,
    outcome: str,
) -> list[str]:
    """
    Raccoglie il reasoning degli agenti specialist per la specifica (market, outcome).
    Usato per costruire la narrativa umana nell'alert Telegram.
    Ogni stringa è "Label: reasoning breve" — max 120 caratteri per readability.
    """
    _LABELS = {
        "stats":   "📊 Stats",
        "odds":    "📈 Mercato",
        "form":    "🏃 Forma",
        "h2h":     "⚔️ H2H",
        "injury":  "🚑 Infortuni",
        "news":    "📰 Notizie",
        "weather": "🌤 Meteo",
    }
    insights: list[str] = []
    for name, result in specialist_results.items():
        if result.failed:
            continue
        for est in result.estimates:
            if est.get("market") == market and est.get("outcome") == outcome:
                txt = (est.get("reasoning") or "").strip()
                if txt and len(txt) > 10:
                    label = _LABELS.get(name, name.capitalize())
                    # Aggiungi confidence/probability per contesto numerico
                    confidence = est.get("probability") or est.get("confidence") or 0.0
                    conf_pct = int(confidence * 100) if confidence else 0
                    insights.append(f"{label}: {txt[:120]} (conf: {conf_pct}%)")
                break
    return insights


def _agent_signal_multiplier(signal: float) -> float:
    """
    Moltiplicatore affidabilità basato sul segnale degli agenti.
    Agenti molto concordi → boost, agenti discordanti → penalità.
    """
    if signal > 0.65:
        return 1.10   # agenti confermano Pinnacle → boost 10%
    elif signal >= 0.45:
        return 1.0    # neutro
    else:
        return 0.85   # agenti dubitano → penalità 15%


# ── Context builder ────────────────────────────────────────────────────────────

async def _build_context(match: Match, db: AsyncSession, all_odds: list[dict]) -> dict:
    from sqlalchemy.orm import selectinload
    match_result = await db.execute(
        select(Match)
        .options(selectinload(Match.competition))
        .where(Match.id == match.id)
    )
    match = match_result.scalar_one()
    competition_name = match.competition.name if match.competition else ""
    stats = match.raw_stats or {}

    # Usa Elo come proxy di forma se standings assenti
    form_stats = stats.get("form")
    if not form_stats or (not form_stats.get("home") and not form_stats.get("away")):
        if stats.get("elo"):
            form_stats = stats["elo"]  # ClubElo fallback

    available = {k: v for k, v in stats.items() if v}
    if stats.get("elo"):
        available["elo_ratings"] = stats["elo"]

    # Meteo: segnale per mercati totals calcio
    weather = stats.get("weather", {})
    weather_note = ""
    if weather:
        impact = weather.get("totals_impact", "neutral")
        cond   = weather.get("conditions", "")
        wind   = weather.get("wind_kmh", 0)
        precip = weather.get("precipitation_mm", 0)
        if impact in ("under_bias", "slight_under"):
            weather_note = (
                f"{cond} — vento {wind:.0f} km/h, pioggia {precip:.1f} mm. "
                f"Condizioni sfavorevoli al gioco aperto ({impact.replace('_', ' ')})."
            )

    # Infortuni NBA: impatto alto se titolari out
    nba_injury_note = ""
    if isinstance(stats.get("injuries"), dict):
        home_impact = stats["injuries"].get("home_impact", "none")
        away_impact = stats["injuries"].get("away_impact", "none")
        if home_impact == "high":
            nba_injury_note += "HOME team: giocatori chiave OUT. "
        elif home_impact == "medium":
            nba_injury_note += "HOME team: giocatori in dubbio. "
        if away_impact == "high":
            nba_injury_note += "AWAY team: giocatori chiave OUT."
        elif away_impact == "medium":
            nba_injury_note += "AWAY team: giocatori in dubbio."

    # Dunkest: stats giocatori chiave NBA
    dunkest_note = ""
    dunkest_data = stats.get("dunkest") or {}
    if dunkest_data:
        parts = []
        for player_name, pdata in dunkest_data.items():
            avg5 = pdata.get("recent_avg_5", {})
            b2b  = pdata.get("back_to_back", False)
            pts  = avg5.get("pts", 0)
            reb  = avg5.get("reb", 0)
            ast  = avg5.get("ast", 0)
            b2b_str = " ⚠️ BACK-TO-BACK" if b2b else ""
            parts.append(f"{player_name}: {pts}pt/{reb}reb/{ast}ast (ultimi 5){b2b_str}")
        dunkest_note = " | ".join(parts)

    return {
        "match_name":        match.display_name(),
        "sport":             match.sport,
        "competition":       competition_name,
        "match_date":        str(match.match_date),
        "home_team":         match.home_team,
        "away_team":         match.away_team,
        "player_a":          match.player_a,
        "player_b":          match.player_b,
        "odds":              all_odds,
        "stats":             stats.get("stats"),
        "form_stats":        form_stats,
        "standings":         stats.get("standings"),
        "h2h":               stats.get("h2h"),
        "injuries":          stats.get("injuries"),
        "injury_note":       nba_injury_note or None,
        "news_summary":      stats.get("news"),
        "dunkest_note":      dunkest_note or None,
        "elo":               stats.get("elo"),
        "weather":           weather or None,
        "weather_note":      weather_note or None,
        "available_signals": ", ".join(available.keys()),
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def assess_analysis_completeness(
    match: Match,
    all_odds: list[dict],
    sharp_odds: list[dict],
) -> tuple[str, list[str]]:
    """
    Assess whether a match had complete data for analysis.

    Returns (status: str, missing_reasons: list[str])
      status: "complete" | "incomplete" | "no_data"
      missing_reasons: list of specific data gaps (empty if complete)
    """
    missing = []

    # Check Pinnacle quotes (essential for no-vig calculation)
    if not sharp_odds:
        missing.append("no_pinnacle_quotes")

    # Check minimum number of bookmakers (need 4+ for signal)
    if len(all_odds) < 4:
        missing.append("insufficient_bookmakers")

    # Check stats availability
    if not match.raw_stats or not match.raw_stats.get("stats"):
        missing.append("stats_incomplete")

    # Check form data (team form, standings)
    if not match.raw_stats or not match.raw_stats.get("form"):
        missing.append("form_data_missing")

    # Check injuries (sport-specific)
    if match.sport in ("football", "basketball"):
        if not match.raw_stats or not match.raw_stats.get("injuries"):
            missing.append("injury_data_missing")

    # Check weather (football only)
    if match.sport == "football":
        if not match.raw_stats or not match.raw_stats.get("weather"):
            missing.append("weather_unavailable")

    if missing:
        return ("incomplete", missing)
    return ("complete", [])


def _extract_uncertainty_score(result: AgentResult) -> float:
    if result.failed:
        return 0.4
    for est in result.estimates:
        if est.get("market") == "uncertainty":
            return float(est.get("probability", 0.5))
    return 0.4


def _extract_uncertainty_reasoning(result: AgentResult) -> str:
    if result.failed:
        return ""
    for est in result.estimates:
        if est.get("market") == "uncertainty":
            return est.get("reasoning", "")
    return ""


def _context_reliability_multiplier(uncertainty_score: float) -> float:
    """
    Modulatore dell'affidabilità in base alla qualità del contesto.
    Situazione limpida (bassa incertezza) = boost.
    Incertezza al limite del gate = penalità.
    """
    if uncertainty_score < 0.25:
        return 1.15   # segnali molto chiari → boost 15%
    elif uncertainty_score < 0.35:
        return 1.08   # situazione buona → boost 8%
    elif uncertainty_score < 0.45:
        return 1.0    # normale
    else:
        return 0.90   # vicino al gate → penalità 10%


async def _persist_agent_result(match: Match, result: AgentResult, db: AsyncSession) -> None:
    run = AgentRun(
        match_id=match.id,
        agent_name=result.agent_name,
        status="done" if not result.failed else "failed",
        output_data={"estimates": result.estimates} if result.estimates else None,
        reasoning=result.reasoning or None,
        tokens_used=result.tokens_used,
        duration_ms=result.duration_ms,
        error=result.error,
        completed_at=datetime.now(timezone.utc),
    )
    db.add(run)
    await db.flush()

    # Scrivi AgentVote per ogni stima — alimenta il loop di apprendimento.
    # Dopo il settlement, il Brier score di ciascun agente verrà aggiornato
    # in base a quanto le sue probabilità erano calibrate sul risultato reale.
    for est in (result.estimates or []):
        if est.get("market") and est.get("outcome") and est.get("probability") is not None:
            vote = AgentVote(
                agent_run_id=run.id,
                match_id=match.id,
                market=est["market"],
                outcome=est["outcome"],
                probability=max(0.01, min(0.99, float(est["probability"]))),
                confidence=max(0.0, min(1.0, float(est.get("confidence", 0.5)))),
            )
            db.add(vote)


def _get_clv_blacklist_sync() -> set[str]:
    """Versione sincrona — chiamata via asyncio.to_thread per non bloccare il loop."""
    import redis as redis_lib
    from app.config import settings as _cfg
    r = redis_lib.from_url(_cfg.redis_url_with_auth, decode_responses=True)
    keys = r.keys("clv:blacklist:*")
    return {k.replace("clv:blacklist:", "") for k in keys if r.get(k) == "1"}


async def _get_clv_blacklist() -> set[str]:
    """
    Legge da Redis la lista dei bookmaker blacklistati per CLV negativo.
    Eseguita in un thread separato per non bloccare l'event loop async.
    Restituisce set vuoto se Redis non disponibile (fail-safe).
    """
    try:
        return await asyncio.to_thread(_get_clv_blacklist_sync)
    except Exception:
        return set()


def _compute_elo_agreement(
    market: str,
    outcome: str,
    match: Match,
    elo_data: dict,
    pinnacle_prob: float,
) -> float:
    """
    Calcola il grado di accordo tra Pinnacle no-vig e il modello ELO.

    Restituisce float in [0, 1]:
    - 1.0 = accordo perfetto (ELO e Pinnacle hanno stessa prob)
    - 0.5 = neutro (nessun dato ELO, o mercato non h2h)
    - 0.0 = disaccordo totale (diff > 30%)
    """
    if market != "h2h" or not elo_data:
        return 0.5

    elo_home_prob = elo_data.get("elo_home_win_prob")
    if elo_home_prob is None:
        return 0.5

    home_team  = (match.home_team or "").lower().strip()
    away_team  = (match.away_team or "").lower().strip()
    outcome_l  = outcome.lower().strip()

    if outcome_l == home_team or outcome_l in home_team or home_team in outcome_l:
        elo_prob = float(elo_home_prob)
    elif outcome_l == away_team or outcome_l in away_team or away_team in outcome_l:
        elo_prob = 1.0 - float(elo_home_prob)
    else:
        return 0.5  # Draw o esito non riconosciuto

    gap = abs(pinnacle_prob - elo_prob)
    agreement = max(0.0, 1.0 - gap / 0.30)

    logger.debug(
        "ELO agreement: %s %s — pinnacle=%.3f elo=%.3f gap=%.3f agreement=%.2f",
        market, outcome, pinnacle_prob, elo_prob, gap, agreement,
    )
    return agreement


def _compute_timing_modifier(match_date) -> float:
    """
    Fattore temporale: penalizza le scommesse a ridosso del fischio,
    dove il mercato è già efficiente e l'edge è stato assorbito dai sharp.

    >48h: 1.05 — finestra lunga, più errori nel mercato
    24-48h: 1.0 — range ottimale
    6-24h:  0.95 — mercato si sta chiudendo
    <6h:    0.85 — troppo tardi
    """
    if match_date is None:
        return 1.0
    now = datetime.now(timezone.utc)
    hours_to_game = (match_date - now).total_seconds() / 3600

    if hours_to_game > 48:
        return 1.05
    elif hours_to_game > 24:
        return 1.0
    elif hours_to_game > 6:
        return 0.95
    else:
        return 0.85


async def _send_alert(
    opp: BettingOpportunity,
    match: Match,
    reliability: float,
    reasoning: str = "",
    agent_insights: list[str] | None = None,
    synthesis_narrative: str = "",
) -> None:
    try:
        from app.services.telegram_service import send_opportunity_alert
        await send_opportunity_alert(
            opp, match.display_name(), reliability=reliability, reasoning=reasoning,
            sport=match.sport or "football",
            agent_insights=agent_insights or [],
            synthesis_narrative=synthesis_narrative,
        )
    except Exception as exc:
        logger.warning("Alert failed for opportunity %s: %s", opp.id, exc)
