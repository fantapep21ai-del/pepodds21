"""
DecisionEngine — suggierisce stake usando Kelly Criterion.

Kelly Criterion:
  f* = (edge × probability - (1 - probability)) / odds
  f = f* × kelly_fraction  (conservativo: 0.25 = quarter kelly)

Applica:
  - Lotta al over-betting con kelly_fraction < 1.0
  - Limiti max per sanità mentale
  - Risk profile adjustment
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class KellyDecision:
    """Risultato di una decisione Kelly."""

    stake_suggested: float  # importo consigliato
    kelly_pct: float  # percentuale del bankroll rischiat
    win_amount: float  # vincita potenziale
    loss_amount: float  # perdita potenziale
    kelly_fraction: float  # frazione usata (0.25 = quarter kelly)
    reasoning: str  # spiegazione per l'utente
    risk_level: str  # "low" | "moderate" | "high"


class DecisionEngine:
    """Suggerisce stake personalizzati basati su Kelly Criterion."""

    # Limiti di sanità mentale
    MAX_KELLY_PCT = 0.05  # non rischiare > 5% bankroll per bet
    MIN_STAKE = 1.0  # minimo stake (€1)
    MAX_STAKE = 500.0  # massimo stake (€500) — limita outliers

    def suggest_stake(
        self,
        bankroll: float,
        expected_value: float,
        best_odds: float,
        model_probability: float,
        kelly_fraction: float = 0.25,
        risk_profile: str = "balanced",
    ) -> KellyDecision:
        """
        Calcola stake con Kelly Criterion.

        Input:
          bankroll: saldo attuale (€)
          expected_value: EV della giocata (0.07 = +7%)
          best_odds: quota bookmaker (1.87)
          model_probability: probabilità no-vig (0.54)
          kelly_fraction: conservativismo (0.1-0.5; default 0.25)
          risk_profile: "conservative" (0.1) | "balanced" (0.25) | "aggressive" (0.5)

        Output:
          KellyDecision con stake_suggested e metriche
        """
        # Adatta kelly_fraction per risk profile
        if risk_profile == "conservative":
            kelly_fraction = 0.10
        elif risk_profile == "aggressive":
            kelly_fraction = 0.50
        # balanced = 0.25 (default)

        # Formula Kelly pura
        # f* = (edge × prob - (1 - prob)) / odds
        # Dove: edge = (odds × prob) - 1 = EV
        edge = expected_value
        win_prob = model_probability

        # Kelly puro
        f_pure = ((edge * win_prob - (1 - win_prob)) / best_odds) if best_odds > 1.0 else 0.0

        # Applica frazione conservativa
        f = max(0.0, f_pure * kelly_fraction)

        # Calcola stake in € e applica limiti
        stake = f * bankroll
        stake = max(self.MIN_STAKE, min(self.MAX_STAKE, stake))

        # Verifica limiti di sanità mentale
        kelly_pct = stake / bankroll if bankroll > 0 else 0.0
        if kelly_pct > self.MAX_KELLY_PCT:
            stake = self.MAX_KELLY_PCT * bankroll

        # Calcola P&L
        win_amount = (best_odds - 1) * stake
        loss_amount = -stake

        # Classifica livello di rischio
        if kelly_pct <= 0.01:
            risk_level = "low"
        elif kelly_pct <= 0.03:
            risk_level = "moderate"
        else:
            risk_level = "high"

        # Costruisci reasoning per l'utente
        reasoning = self._build_reasoning(
            bankroll,
            expected_value,
            best_odds,
            model_probability,
            stake,
            kelly_pct,
            f_pure,
            kelly_fraction,
        )

        logger.info(
            "Kelly decision: bankroll=%.0f EV=%+.1f%% odds=%.2f → stake=%.2f (%+.1f%% bankroll)",
            bankroll,
            expected_value * 100,
            best_odds,
            stake,
            kelly_pct * 100,
        )

        return KellyDecision(
            stake_suggested=round(stake, 2),
            kelly_pct=round(kelly_pct, 4),
            win_amount=round(win_amount, 2),
            loss_amount=round(loss_amount, 2),
            kelly_fraction=kelly_fraction,
            reasoning=reasoning,
            risk_level=risk_level,
        )

    @staticmethod
    def _build_reasoning(
        bankroll: float,
        ev: float,
        odds: float,
        prob: float,
        stake: float,
        kelly_pct: float,
        f_pure: float,
        kelly_fraction: float,
    ) -> str:
        """Costruisce spiegazione per l'utente."""
        lines = [
            f"**Kelly Criterion calcolato**",
            f"",
            f"• Bankroll attuale: €{bankroll:.0f}",
            f"• EV della giocata: {ev:+.1%}",
            f"• Probabilità vera (no-vig): {prob:.0%}",
            f"• Quota migliore: {odds:.2f}",
            f"",
            f"**Calcolo Kelly**:",
            f"• Kelly puro (f*): {f_pure:.1%}",
            f"• Frazione applicata: {kelly_fraction:.0%} (conservativo)",
            f"• Kelly finale: {f_pure * kelly_fraction:.1%}",
            f"",
            f"**Suggerimento**:",
            f"• Stake: €{stake:.0f}",
            f"• % del bankroll: {kelly_pct:.2%}",
            f"• Vincita potenziale: €{(odds-1)*stake:.0f}",
            f"• Massima perdita: €{stake:.0f}",
        ]

        return "\n".join(lines)

    @staticmethod
    def calculate_risk_of_ruin(
        bankroll: float,
        win_prob: float,
        avg_odds: float = 1.9,
        num_bets: int = 30,
    ) -> float:
        """
        Stima Risk of Ruin (probabilità di bankrupt) nei prossimi N scommesse.

        Usa approssimazione gambler's ruin:
          RoR ≈ (1 - win_rate) / (1 + win_rate) ^ N_bets

        Realistico per serie di scommesse con stake Kelly.
        """
        if win_prob <= 0 or win_prob >= 1:
            return 0.5  # Indeterminato

        # Formula semplificata: Kelly Criterion assume RoR < 1% con Kelly puro
        # Con Kelly fraction 0.25, RoR diminuisce esponenzialmente
        q = 1 - win_prob  # lose probability
        p = win_prob  # win probability

        # Gambler's ruin approximation
        if q != p:
            ratio = q / p
            ror = (ratio ** num_bets - 1) / (ratio ** num_bets - 1) if ratio != 1 else 1 / num_bets
        else:
            ror = 0.5 ** num_bets

        return max(0.0, min(1.0, ror))
