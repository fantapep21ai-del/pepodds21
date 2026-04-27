"""
Bet Builder — constructs Singles, Doubles, and Multiples from opportunities.

Priority order:
  1. Singles (Tier S, odds 1.65–2.40) — PRIMARY output
  2. Doubles (2× Tier A/B, correlation < 0.25, EV ≥ 5%)
  3. Multiples (4–6× Tier B, EV ≥ 8%)

NO BET rule:
  If fewer than 2 valid events (Tier S or A), return empty list and log "NO BET TODAY".

Correlation proxy:
  Two legs are considered correlated if they share the same match
  (same match_id) or the same competition. We keep correlation simple
  because we don't have real covariance data: same match = correlation 1.0,
  same competition = 0.40, different = 0.10.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from math import prod
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

MAX_SINGLE_KELLY = 0.25     # fractional Kelly multiplier
MAX_STAKE_PCT = 0.03        # 3% bankroll hard cap


@runtime_checkable
class OpportunityLike(Protocol):
    """Duck-type protocol so we can use both ORM objects and test stubs."""
    match_id: object
    best_odds: float
    model_probability: float
    expected_value: float
    tier: str
    suggested_stake: float | None


@dataclass
class BetRecommendation:
    bet_type: str                          # single | double | multiple
    opportunities: list                    # list of opportunity objects
    combined_odds: float
    combined_prob: float
    expected_value: float
    stake: float
    note: str = ""


def build_recommendations(
    opportunities: list,
    bankroll: float,
    base_single_stake: float | None = None,
) -> list[BetRecommendation]:
    """
    Build bet recommendations from a list of classified opportunities.

    Args:
        opportunities:     list of BettingOpportunity ORM objects with tier set
        bankroll:          current bankroll in euros
        base_single_stake: override for single bet stake (default: Kelly-derived)

    Returns:
        List of BetRecommendation. Empty list = NO BET TODAY.
    """
    tier_s = [o for o in opportunities if o.tier == "S"]
    tier_a = [o for o in opportunities if o.tier == "A"]
    tier_b = [o for o in opportunities if o.tier == "B"]

    # ── NO BET RULE ───────────────────────────────────────────────────────────
    # Il NO BET è la conclusione naturale dell'analisi AI:
    # se non è emersa alcuna quota di valore reale in nessun tier, non si punta.
    if not tier_s and not tier_a and not tier_b:
        logger.info("NO BET TODAY — nessuna quota di valore trovata dall'analisi.")
        return []

    results: list[BetRecommendation] = []

    # ── 1. SINGLES from Tier S ────────────────────────────────────────────────
    for opp in tier_s:
        if not (1.65 <= opp.best_odds <= 2.40):
            continue  # odds outside primary window

        stake = base_single_stake or _kelly_stake(opp, bankroll)
        if stake <= 0:
            continue

        results.append(BetRecommendation(
            bet_type="single",
            opportunities=[opp],
            combined_odds=opp.best_odds,
            combined_prob=opp.model_probability,
            expected_value=opp.expected_value,
            stake=round(stake, 2),
            note=f"Tier S single — EV {opp.expected_value:.1%}",
        ))

    # ── 2. DOUBLES from Tier A + B ────────────────────────────────────────────
    tier_ab = tier_a + tier_b
    if len(tier_ab) >= 2:
        best_pairs = _find_best_doubles(tier_ab)
        for o1, o2 in best_pairs:
            combined_prob = o1.model_probability * o2.model_probability
            combined_odds = o1.best_odds * o2.best_odds
            ev = combined_prob * combined_odds - 1

            if ev < 0.05:
                continue  # EV threshold for doubles

            single_stake = base_single_stake or _kelly_stake(o1, bankroll)
            stake = round(0.6 * single_stake, 2)
            if stake < 1.0:
                continue

            results.append(BetRecommendation(
                bet_type="double",
                opportunities=[o1, o2],
                combined_odds=round(combined_odds, 4),
                combined_prob=round(combined_prob, 6),
                expected_value=round(ev, 4),
                stake=stake,
                note=f"Double Tier A/B — EV {ev:.1%}, corr<0.25",
            ))
            break  # one double max per run to control exposure

    # ── 3. MULTIPLE from Tier B ───────────────────────────────────────────────
    if len(tier_b) >= 4:
        legs = _select_multiple_legs(tier_b)
        if legs:
            combined_prob = prod(o.model_probability for o in legs)
            combined_odds = prod(o.best_odds for o in legs)
            ev = combined_prob * combined_odds - 1

            if ev >= 0.08:
                single_stake = base_single_stake or _kelly_stake(legs[0], bankroll)
                stake = round(0.2 * single_stake, 2)
                if stake >= 1.0:
                    results.append(BetRecommendation(
                        bet_type="multiple",
                        opportunities=legs,
                        combined_odds=round(combined_odds, 4),
                        combined_prob=round(combined_prob, 6),
                        expected_value=round(ev, 4),
                        stake=stake,
                        note=f"Multiple {len(legs)} legs Tier B — EV {ev:.1%}",
                    ))

    logger.info(
        "Bet builder: %d singles, doubles/multiples included — total %d recommendations",
        sum(1 for r in results if r.bet_type == "single"), len(results),
    )
    return results


# ── Internal helpers ──────────────────────────────────────────────────────────

def _kelly_stake(opp, bankroll: float) -> float:
    """Quarter-Kelly with 3% bankroll hard cap."""
    b = opp.best_odds - 1.0
    if b <= 0:
        return 0.0
    p = opp.model_probability
    q = 1.0 - p
    kelly_f = (b * p - q) / b
    if kelly_f <= 0:
        return 0.0
    fraction = min(kelly_f * MAX_SINGLE_KELLY, MAX_STAKE_PCT)
    return round(bankroll * fraction, 2)


def _correlation(o1, o2) -> float:
    """
    Proxy correlation between two legs.
    Same match: 1.0 (always correlated — never combine)
    Different match, same competition: 0.40
    Different competition: 0.10
    """
    if str(o1.match_id) == str(o2.match_id):
        return 1.0
    if hasattr(o1, "competition_id") and hasattr(o2, "competition_id"):
        if str(getattr(o1, "competition_id", "")) == str(getattr(o2, "competition_id", "")):
            return 0.40
    return 0.10


def _find_best_doubles(tier_ab: list) -> list[tuple]:
    """Find best non-correlated pairs sorted by combined EV."""
    pairs: list[tuple] = []
    for i, o1 in enumerate(tier_ab):
        for o2 in tier_ab[i + 1:]:
            if _correlation(o1, o2) >= 0.25:
                continue
            combined_ev = o1.model_probability * o2.model_probability * o1.best_odds * o2.best_odds - 1
            pairs.append((combined_ev, o1, o2))

    pairs.sort(reverse=True)
    return [(o1, o2) for _, o1, o2 in pairs[:3]]  # top 3 pairs


def _select_multiple_legs(tier_b: list) -> list:
    """
    Select 4–6 legs for a multiple.
    Rules: all from different matches, max 4 legs (conservative).
    """
    selected: list = []
    seen_matches: set = set()

    # Sort by EV descending
    candidates = sorted(tier_b, key=lambda o: o.expected_value, reverse=True)

    for opp in candidates:
        mid = str(opp.match_id)
        if mid in seen_matches:
            continue
        if _correlation(opp, candidates[0]) >= 0.25 and opp != candidates[0]:
            continue
        selected.append(opp)
        seen_matches.add(mid)
        if len(selected) >= 4:  # cap at 4 for multiples
            break

    return selected if len(selected) >= 4 else []
