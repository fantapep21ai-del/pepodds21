"""
Consensus engine — Pinnacle no-vig first.

Funzioni esportate:
  compute_no_vig(sharp_odds)          → probabilità reali per (market, outcome)
  find_value_opportunities(...)        → lista candidati con EV > soglia
  compute_reliability(...)             → score 0-1 affidabilità della giocata
  run_consensus(...)                   → compatibilità backward (usato dai test)
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import NamedTuple

from app.agents.base import AgentResult

logger = logging.getLogger(__name__)

MIN_EV_THRESHOLD    = 0.03    # +3% EV minimo (allineato a pipeline.py che usa min_ev=0.03)
UNCERTAINTY_GATE    = 0.70   # allineato a pipeline.py — run_consensus è solo backward-compat per test
MIN_CONSENSUS_PROB  = 0.10   # permette pareggi e outsider nel calcio (prob >10% = odds <10.0)

SHARP_BOOKMAKERS = {"pinnacle", "betfair_ex_eu"}


# ── No-vig calculation ────────────────────────────────────────────────────────

def compute_no_vig(sharp_odds: list[dict]) -> dict[tuple[str, str], float]:
    """
    Calcola probabilità no-vig dai bookmaker sharp.
    Restituisce {(market, outcome): probability}.

    Metodo: normalizza le probabilità implicite rimuovendo il margine (vig).
    Pinnacle ha margine ~2%, Betfair ~0% (exchange puro).
    """
    market_outcomes: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

    for o in sharp_odds:
        if o["market"] in ("h2h", "totals"):
            market_outcomes[o["market"]][o["outcome"]].append(float(o["odds"]))

    result: dict[tuple[str, str], float] = {}

    for market, outcomes in market_outcomes.items():
        avg_odds = {
            outcome: sum(prices) / len(prices)
            for outcome, prices in outcomes.items()
        }
        implied = {
            outcome: 1.0 / odds
            for outcome, odds in avg_odds.items()
            if odds > 1.01
        }
        total_implied = sum(implied.values())
        if total_implied <= 0:
            continue

        vig = total_implied - 1.0
        logger.debug("Market %s: vig=%.2f%% — outcomes: %s", market, vig * 100, list(outcomes.keys()))

        for outcome, imp_prob in implied.items():
            no_vig_prob = imp_prob / total_implied
            result[(market, outcome)] = round(no_vig_prob, 6)

    return result


# ── Value detection ───────────────────────────────────────────────────────────

def find_value_opportunities(
    pinnacle_probs: dict[tuple[str, str], float],
    soft_odds: list[dict],
    min_ev: float = MIN_EV_THRESHOLD,
) -> list[dict]:
    """
    Confronta probabilità no-vig con le migliori quote soft.
    Ritorna lista di candidati con EV > min_ev, ordinati per EV decrescente.

    Per ogni esito trova:
    - Miglior quota tra tutti i soft bookmaker
    - Quanti bookmaker confermano il valore (quote > 1/no_vig_prob)
    - EV = (no_vig_prob × best_soft_odds) - 1
    """
    # Aggrega tutte le quote soft per (market, outcome)
    soft_by_key: dict[tuple[str, str], list[tuple[float, str]]] = defaultdict(list)
    for o in soft_odds:
        key = (o["market"], o["outcome"])
        soft_by_key[key].append((float(o["odds"]), o["bookmaker"]))

    candidates: list[dict] = []

    for (market, outcome), no_vig_prob in pinnacle_probs.items():
        if no_vig_prob < MIN_CONSENSUS_PROB:
            continue
        if (market, outcome) not in soft_by_key:
            continue

        odds_list = soft_by_key[(market, outcome)]
        best_odds_val, best_bookmaker = max(odds_list, key=lambda x: x[0])
        ev = (no_vig_prob * best_odds_val) - 1.0

        # Quanti bookmaker soft offrono una quota con EV > 0
        fair_odds = 1.0 / no_vig_prob  # quota fair = nessun margine
        n_confirming = sum(1 for odds, _ in odds_list if odds > fair_odds)

        if ev >= min_ev:
            candidates.append({
                "market": market,
                "outcome": outcome,
                "bookmaker": best_bookmaker,
                "best_odds": best_odds_val,
                "no_vig_prob": no_vig_prob,
                "ev": ev,
                "n_confirming": n_confirming,
                "fair_odds": round(fair_odds, 3),
            })
            logger.info(
                "VALUE: %s %s — no_vig=%.3f fair=%.2f best=%.2f @%s EV=%+.1f%% (%d bk confermano)",
                market, outcome, no_vig_prob, fair_odds,
                best_odds_val, best_bookmaker, ev * 100, n_confirming,
            )

    candidates.sort(key=lambda c: c["ev"], reverse=True)
    return candidates


# ── Reliability score ─────────────────────────────────────────────────────────

def compute_reliability(
    ev: float,
    uncertainty_score: float,
    n_confirming: int,
    reference_source: str = "pinnacle_no_vig",
    elo_agreement: float = 0.5,
    timing_modifier: float = 1.0,
) -> float:
    """
    Calcola un indice di affidabilità della giocata tra 0 e 1.

    Componenti:
    - ev_factor:          quanto è grande l'edge (max a EV=20%)
    - uncertainty_factor: quanto è prevedibile la partita (1 - uncertainty)
    - agreement_factor:   quanti bookmaker confermano il valore
    - source_factor:      Pinnacle > fallback agent consensus
    - elo_factor:         accordo tra Pinnacle e ELO (0=disaccordo, 1=accordo)
    - timing_modifier:    fattore temporale (penalizza <6h al fischio, boost >48h)

    Restituisce float in [0.05, 0.92]
    """
    # Edge quality: scala lineare da 0 a 1 per EV 0→20%
    ev_factor = min(ev / 0.20, 1.0)

    # Prevedibilità: meno incertezza = più affidabile
    uncertainty_factor = max(0.0, 1.0 - uncertainty_score)

    # Accordo bookmaker: 1 bk = bassa conf, 3+ bk = alta conf
    agreement_factor = min(n_confirming / 3.0, 1.0)

    # Fonte: Pinnacle no-vig è gold standard
    source_factor = 1.0 if reference_source == "pinnacle_no_vig" else 0.65

    # ELO agreement: 0=ELO contraddice Pinnacle, 0.5=neutro, 1=ELO conferma
    # Scala a [0.4, 1.0] per evitare che un singolo fattore collassi la reliability
    elo_factor = 0.4 + elo_agreement * 0.6

    # Combina: weighted geometric mean (pesi ridistribuiti con ELO)
    raw = (
        (ev_factor ** 0.30)
        * (uncertainty_factor ** 0.30)
        * (agreement_factor ** 0.18)
        * (source_factor ** 0.10)
        * (elo_factor ** 0.12)
    )

    # Fattore temporale: applicato come moltiplicatore lineare (non nella geo-mean)
    raw = raw * timing_modifier

    # Clip ragionevole: mai sotto 5% né sopra 92% (onestà vs utente)
    reliability = max(0.05, min(0.92, raw))

    logger.debug(
        "Reliability: ev=%.2f unc=%.2f agr=%d src=%s elo=%.2f timing=%.2f → %.1f%%",
        ev, uncertainty_score, n_confirming, reference_source,
        elo_agreement, timing_modifier, reliability * 100,
    )
    return reliability


# ── Backward-compat (run_consensus usato da test/altri moduli) ────────────────

class OpportunityCandidate(NamedTuple):
    market: str
    outcome: str
    bookmaker: str
    best_odds: float
    model_probability: float
    expected_value: float
    consensus_votes: dict
    uncertainty_score: float
    reference_source: str = "pinnacle_no_vig"


def run_consensus(
    agent_results: list[AgentResult],
    available_odds: list[dict],
    agent_weights: dict[str, float],
) -> tuple[list[OpportunityCandidate], float]:
    """Interfaccia backward-compat. Il pipeline principale non la usa più."""
    sharp = [o for o in available_odds if o["bookmaker"] in SHARP_BOOKMAKERS]
    soft  = [o for o in available_odds if o["bookmaker"] not in SHARP_BOOKMAKERS]

    uncertainty_score = _extract_uncertainty(agent_results)
    if uncertainty_score >= UNCERTAINTY_GATE:
        return [], uncertainty_score

    pinnacle_probs = compute_no_vig(sharp)
    if pinnacle_probs:
        candidates = find_value_opportunities(pinnacle_probs, soft)
        opps = [
            OpportunityCandidate(
                market=c["market"],
                outcome=c["outcome"],
                bookmaker=c["bookmaker"],
                best_odds=c["best_odds"],
                model_probability=c["no_vig_prob"],
                expected_value=c["ev"],
                consensus_votes={"n_confirming": c["n_confirming"]},
                uncertainty_score=uncertainty_score,
                reference_source="pinnacle_no_vig",
            )
            for c in candidates
        ]
        return opps, uncertainty_score

    return [], uncertainty_score


def _extract_uncertainty(agent_results: list[AgentResult]) -> float:
    for result in agent_results:
        if result.agent_name == "uncertainty" and not result.failed:
            for est in result.estimates:
                if est.get("market") == "uncertainty":
                    return float(est.get("probability", 0.5))
    return 0.4
