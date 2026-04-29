"""
Tier classification engine for betting opportunities.

Tier S: EV >= 8%  → massima priorità, singola
Tier A: EV >= 5%  → alta priorità, singola
Tier B: EV >= 3%  → normale, scalata se quota 1.30–1.80
Tier C: EV < 3%   → scartata

La soglia di incertezza è gestita a monte dall'UncertaintyAgent (gate 0.70 in pipeline.py).
Qui classifichiamo solo in base all'EV matematico e alla quota.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TierResult:
    tier: str             # S | A | B | C
    edge: float           # EV grezzo
    confidence: float     # 1 - uncertainty_score
    bet_type: str         # singola | scalata | doppia | multipla
    confidence_level: str # alta | normale | bassa (UI label)


def classify(
    expected_value: float,
    uncertainty_score: float,
    model_probability: float,
    best_odds: float,
) -> TierResult:
    """
    Classifica una opportunità in un tier.

    Logica:
    - EV >= 8%  → Tier S (massima priorità)
    - EV >= 5%  → Tier A
    - EV >= 3%  → Tier B
    - altrimenti → Tier C (scartata)

    Bet type:
    - Quota 1.30–1.80 → candidata scalata (compounding sequenziale)
    - Quota > 1.80    → singola
    """
    confidence = max(0.0, 1.0 - uncertainty_score)
    edge = expected_value

    # ── Tier assignment (EV-driven) ───────────────────────────────────────────
    if expected_value >= 0.08:
        tier = "S"
    elif expected_value >= 0.05:
        tier = "A"
    elif expected_value >= 0.03:
        tier = "B"
    else:
        tier = "C"

    # ── Bet type ──────────────────────────────────────────────────────────────
    # Tutto "singola" — sistema semplificato, niente scalate
    bet_type = "singola"

    # ── UI label ──────────────────────────────────────────────────────────────
    if tier == "S":
        confidence_level = "alta"
    elif tier == "A":
        confidence_level = "normale"
    else:
        confidence_level = "bassa"

    return TierResult(
        tier=tier,
        edge=round(edge, 6),
        confidence=round(confidence, 4),
        bet_type=bet_type,
        confidence_level=confidence_level,
    )


def min_ev_for_tier(tier: str) -> float:
    return {"S": 0.08, "A": 0.05, "B": 0.03, "C": 0.0}.get(tier, 0.0)
