"""
Kelly Criterion Bet Sizing Service.

Calcola la dimensione ottimale della scommessa basata su:
  - Expected Value (EV)
  - Probabilità del modello
  - Quote decimali
  - Bankroll corrente
  - Livello di confidenza (affidabilità)

Formula Kelly completa:
  f* = (p * odds - 1) / (odds - 1)
  dove p = probabilità stimata, odds = quota decimale

Usiamo "Fractional Kelly" per ridurre il rischio:
  - quarter-Kelly (25%): conservativo, raccomandato per nuovo sistema
  - half-Kelly (50%): moderato, dopo validazione statistica
  - full-Kelly: mai per sistema live (troppo volatile)

Limiti di sicurezza:
  - Max stake per scommessa: 5% del bankroll
  - Min stake: €5 (sotto questa soglia non vale la commissione)
  - Max totale in gioco contemporaneamente: 20% del bankroll
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ── Configurazione globale ────────────────────────────────────────────────────
KELLY_FRACTION   = 0.25    # quarter-Kelly: conservativo ma efficace
MAX_STAKE_PCT    = 0.05    # massimo 5% del bankroll per singola bet
MIN_STAKE_EUR    = 5.0     # stake minimo in euro
MAX_EXPOSURE_PCT = 0.20    # massimo 20% del bankroll in gioco contemporaneamente

# Modificatori Kelly per tier di confidenza
CONFIDENCE_KELLY_MULTIPLIER: dict[str, float] = {
    "high":   1.00,   # piena fiducia nel modello
    "medium": 0.75,   # riduzione 25% per incertezza media
    "low":    0.50,   # riduzione 50% per bassa confidenza
}

# Modificatori per tipo di scommessa
BET_TYPE_MULTIPLIER: dict[str, float] = {
    "singola":   1.00,
    "sistema":   0.80,
    "antepost":  0.60,   # ante-post hanno più incertezza
}


@dataclass
class KellyResult:
    """Risultato del calcolo Kelly."""
    optimal_fraction: float        # f* Kelly puro [0, 1]
    adjusted_fraction: float       # f* dopo tutti i modificatori
    recommended_stake: float       # stake raccomandato in EUR
    max_stake: float               # cap massimo basato su bankroll
    min_stake: float               # stake minimo
    is_valid: bool                 # True se la bet ha EV positivo
    kelly_edge: float              # edge Kelly (EV relativo alle odds)
    reasoning: str                 # spiegazione del calcolo


def compute_kelly_stake(
    model_prob: float,
    decimal_odds: float,
    bankroll: float,
    confidence_level: str = "medium",
    bet_type: str = "singola",
    existing_exposure: float = 0.0,
    ev: Optional[float] = None,
) -> KellyResult:
    """
    Calcola lo stake ottimale via Kelly Criterion.

    Args:
        model_prob:        probabilità stimata dal modello [0, 1]
        decimal_odds:      quota decimale (es. 1.85)
        bankroll:          bankroll totale corrente in EUR
        confidence_level:  "high" | "medium" | "low"
        bet_type:          "singola" | "sistema" | "antepost"
        existing_exposure: stake già in gioco (per cap esposizione totale)
        ev:                EV già calcolato (opzionale, come check)

    Returns:
        KellyResult con stake raccomandato e metadati
    """
    if bankroll <= 0 or decimal_odds <= 1.0 or model_prob <= 0 or model_prob >= 1:
        return KellyResult(
            optimal_fraction=0.0, adjusted_fraction=0.0,
            recommended_stake=0.0, max_stake=0.0, min_stake=MIN_STAKE_EUR,
            is_valid=False, kelly_edge=0.0,
            reasoning="Parametri non validi",
        )

    # ── Kelly formula ─────────────────────────────────────────────────────────
    # f* = (p * b - q) / b  dove b = odds - 1, q = 1 - p
    b = decimal_odds - 1.0
    q = 1.0 - model_prob
    f_star = (model_prob * b - q) / b

    # EV di check
    calculated_ev = (model_prob * decimal_odds) - 1.0
    if ev is not None:
        # Usa l'EV già calcolato come cross-check
        if abs(calculated_ev - ev) > 0.05:
            logger.debug(
                "Kelly EV mismatch: model %.3f vs provided %.3f", calculated_ev, ev
            )

    # Se EV negativo → non scommettere
    if f_star <= 0 or calculated_ev <= 0:
        return KellyResult(
            optimal_fraction=0.0, adjusted_fraction=0.0,
            recommended_stake=0.0, max_stake=bankroll * MAX_STAKE_PCT,
            min_stake=MIN_STAKE_EUR, is_valid=False,
            kelly_edge=f_star,
            reasoning=f"EV negativo ({calculated_ev:.1%}) — nessuna scommessa",
        )

    # ── Applicazione modificatori ─────────────────────────────────────────────
    kelly_mod   = KELLY_FRACTION
    conf_mod    = CONFIDENCE_KELLY_MULTIPLIER.get(confidence_level, 0.75)
    bet_mod     = BET_TYPE_MULTIPLIER.get(bet_type, 1.0)

    adjusted_f = f_star * kelly_mod * conf_mod * bet_mod

    # ── Calcolo stake ─────────────────────────────────────────────────────────
    raw_stake = adjusted_f * bankroll

    # Cap massimo: 5% del bankroll
    max_stake = bankroll * MAX_STAKE_PCT

    # Cap esposizione totale: se siamo già al 15% in gioco, riduci ulteriormente
    remaining_exposure = bankroll * MAX_EXPOSURE_PCT - existing_exposure
    if remaining_exposure <= 0:
        return KellyResult(
            optimal_fraction=f_star, adjusted_fraction=adjusted_f,
            recommended_stake=0.0, max_stake=max_stake,
            min_stake=MIN_STAKE_EUR, is_valid=False,
            kelly_edge=f_star,
            reasoning=f"Esposizione massima raggiunta ({existing_exposure:.0f}€ in gioco)",
        )

    # Applica tutti i cap
    final_stake = min(raw_stake, max_stake, remaining_exposure)

    # Arrotonda a €5 più vicini (più professionale)
    final_stake = max(MIN_STAKE_EUR, round(final_stake / 5) * 5)

    # Costruisci reasoning
    reasoning_parts = [
        f"Kelly puro: {f_star:.1%}",
        f"Fraction {KELLY_FRACTION:.0%} × conf {conf_mod:.0%} × tipo {bet_mod:.0%}",
        f"= {adjusted_f:.2%} × {bankroll:.0f}€ bankroll",
        f"= {raw_stake:.0f}€ → cap {final_stake:.0f}€",
    ]
    if final_stake < raw_stake:
        reasoning_parts.append(f"(cappato da {'max_stake' if max_stake < remaining_exposure else 'max_exposure'})")

    return KellyResult(
        optimal_fraction  = round(f_star, 4),
        adjusted_fraction = round(adjusted_f, 4),
        recommended_stake = final_stake,
        max_stake         = max_stake,
        min_stake         = MIN_STAKE_EUR,
        is_valid          = True,
        kelly_edge        = round(f_star, 4),
        reasoning         = " | ".join(reasoning_parts),
    )


async def get_kelly_stake_for_opportunity(
    opportunity,
    db,
) -> KellyResult:
    """
    Wrapper che legge il bankroll dal DB e calcola il Kelly stake
    per una BettingOpportunity specifica.

    Args:
        opportunity: istanza BettingOpportunity
        db:          AsyncSession

    Returns:
        KellyResult — usa .recommended_stake per lo stake suggerito
    """
    try:
        from sqlalchemy import select, func
        from app.db.models.bankroll import Bankroll
        from app.db.models.bet import Bet

        # Leggi bankroll corrente
        bankroll_result = await db.execute(
            select(func.sum(Bankroll.amount)).where(Bankroll.is_active == True)
        )
        bankroll = float(bankroll_result.scalar() or 1000.0)

        # Calcola esposizione corrente (bet aperte)
        exposure_result = await db.execute(
            select(func.sum(Bet.stake)).where(
                Bet.status.in_(["pending", "in_attesa"])
            )
        )
        existing_exposure = float(exposure_result.scalar() or 0.0)

        return compute_kelly_stake(
            model_prob        = float(opportunity.model_probability or 0.5),
            decimal_odds      = float(opportunity.best_odds or 1.5),
            bankroll          = bankroll,
            confidence_level  = opportunity.confidence_level or "medium",
            bet_type          = opportunity.bet_type or "singola",
            existing_exposure = existing_exposure,
            ev                = float(opportunity.expected_value or 0.0),
        )

    except Exception as exc:
        logger.warning("Kelly stake calculation failed: %s", exc)
        return KellyResult(
            optimal_fraction=0.0, adjusted_fraction=0.0,
            recommended_stake=10.0, max_stake=50.0,
            min_stake=MIN_STAKE_EUR, is_valid=True,
            kelly_edge=0.0, reasoning="Fallback stake €10 (errore calcolo)",
        )


def format_kelly_for_telegram(result: KellyResult) -> str:
    """Formatta il risultato Kelly per il messaggio Telegram."""
    if not result.is_valid:
        return f"⚠️ {result.reasoning}"

    edge_pct = result.kelly_edge * 100
    return (
        f"💰 Stake suggerito: <b>€{result.recommended_stake:.0f}</b>\n"
        f"   Kelly edge: {edge_pct:.1f}% | {result.reasoning.split('|')[0].strip()}"
    )
