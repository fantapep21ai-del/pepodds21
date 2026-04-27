"""
RiskMonitor — monitora la salute del bankroll durante la sessione.

Metriche:
  - exposure_pct: percentuale del bankroll a rischio
  - max_loss_scenario: peggiore caso nei prossimi N bets
  - risk_of_ruin: probabilità di bankrupt
  - correlation_risk: bets sono dipendenti? (stessa partita/league)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.db.models.bet import Bet

logger = logging.getLogger(__name__)


@dataclass
class BankrollHealth:
    """Valutazione della salute del bankroll."""

    bankroll: float
    exposure_pct: float  # % soldi a rischio
    max_loss_worst_case: float  # se perdi i prossimi 3 bets
    risk_of_ruin_pct: float  # probabilità bankrupt in 30gg
    correlation_risk: float  # 0=indipendenti, 1=tutti stessa partita
    status: str  # "healthy" | "caution" | "danger"
    recommended_max_bet: float
    daily_budget: int  # numero di bets suggerito per giorno
    warnings: list[str]


class RiskMonitor:
    """Monitora rischi di bankroll e exposure."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def evaluate_bankroll(
        self,
        bankroll: float,
        lookback_days: int = 30,
        confidence_threshold: float = 0.65,
    ) -> BankrollHealth:
        """
        Valuta salute del bankroll.

        Legge bets aperti e recenti per calcolare metriche di rischio.
        """
        from app.db.models.bet import Bet
        from app.db.models.opportunity import BettingOpportunity

        now = datetime.now(timezone.utc)
        lookback = now - timedelta(days=lookback_days)

        # Carica bets aperti
        open_result = await self.db.execute(
            select(Bet).where(Bet.status == "open")
        )
        open_bets = open_result.scalars().all()

        # Carica bets recenti per statistiche
        recent_result = await self.db.execute(
            select(Bet)
            .where(Bet.created_at >= lookback)
            .order_by(Bet.created_at.desc())
        )
        recent_bets = recent_result.scalars().all()

        # Calcola metriche
        exposure_pct = self._calculate_exposure(bankroll, open_bets)
        max_loss = self._calculate_max_loss_scenario(open_bets)
        ror_pct = self._estimate_risk_of_ruin(recent_bets, bankroll)
        correlation = self._calculate_correlation_risk(open_bets)

        # Determina status
        if exposure_pct > 0.10 or ror_pct > 0.05:
            status = "danger"
        elif exposure_pct > 0.05 or ror_pct > 0.02:
            status = "caution"
        else:
            status = "healthy"

        # Raccomandazioni
        recommended_max_bet = self._recommended_max_bet(
            bankroll, exposure_pct, status
        )
        daily_budget = max(1, int(10 / max(exposure_pct, 0.01)))

        # Warnings
        warnings = self._generate_warnings(
            exposure_pct, ror_pct, status, correlation
        )

        logger.info(
            "Bankroll health: %s | exposure=%.1f%% | ror=%.1f%% | corr=%.2f",
            status,
            exposure_pct * 100,
            ror_pct * 100,
            correlation,
        )

        return BankrollHealth(
            bankroll=bankroll,
            exposure_pct=round(exposure_pct, 4),
            max_loss_worst_case=round(max_loss, 2),
            risk_of_ruin_pct=round(ror_pct * 100, 2),
            correlation_risk=round(correlation, 2),
            status=status,
            recommended_max_bet=round(recommended_max_bet, 2),
            daily_budget=daily_budget,
            warnings=warnings,
        )

    @staticmethod
    def _calculate_exposure(bankroll: float, open_bets: list) -> float:
        """Percentuale del bankroll a rischio nei bets aperti."""
        if not open_bets or bankroll <= 0:
            return 0.0

        total_at_risk = sum(float(b.stake) for b in open_bets)
        return total_at_risk / bankroll

    @staticmethod
    def _calculate_max_loss_scenario(open_bets: list) -> float:
        """Peggiore caso: perdi i prossimi 3 bets più grandi."""
        if not open_bets:
            return 0.0

        stakes = sorted([float(b.stake) for b in open_bets], reverse=True)
        return sum(stakes[:3])

    @staticmethod
    def _estimate_risk_of_ruin(recent_bets: list, bankroll: float) -> float:
        """Stima RoR dai dati recenti (30gg)."""
        if not recent_bets or bankroll <= 0:
            return 0.01  # Default conservativo

        settled_bets = [b for b in recent_bets if b.status in ("won", "lost")]
        if not settled_bets:
            return 0.01

        # Calcola win rate
        wins = sum(1 for b in settled_bets if b.status == "won")
        win_prob = wins / len(settled_bets)

        # Gambler's ruin approximation su 30 prossime scommesse
        if win_prob == 0.5:
            return 0.01  # Coin flip
        if win_prob <= 0:
            return 1.0
        if win_prob >= 1:
            return 0.0

        q = 1 - win_prob
        p = win_prob
        ratio = q / p
        ror = (ratio ** 30) / (ratio ** 30 + 1) if ratio > 0 else 0.5

        return min(1.0, max(0.0, ror))

    @staticmethod
    def _calculate_correlation_risk(open_bets: list) -> float:
        """Rischio che tutti i bets falliscano insieme (stessa partita?)."""
        if not open_bets or len(open_bets) < 2:
            return 0.0

        # Conta quanti bets sono sulla stessa partita
        match_ids = {}
        for b in open_bets:
            # Leggi opportunity.match_id
            match_id = getattr(b, "opportunity", None)
            if match_id:
                match_ids[str(match_id)] = match_ids.get(str(match_id), 0) + 1

        if not match_ids:
            return 0.0

        # Risk = max(bets sulla stessa partita) / total bets
        max_same_match = max(match_ids.values())
        correlation = max_same_match / len(open_bets)

        return correlation

    @staticmethod
    def _recommended_max_bet(
        bankroll: float, current_exposure: float, status: str
    ) -> float:
        """Stake massimo suggerito basato su salute bankroll."""
        if status == "danger":
            return bankroll * 0.01  # 1% max
        elif status == "caution":
            return bankroll * 0.02  # 2% max
        else:
            return bankroll * 0.03  # 3% max

    @staticmethod
    def _generate_warnings(
        exposure: float, ror: float, status: str, correlation: float
    ) -> list[str]:
        """Genera liste di warning."""
        warnings = []

        if exposure > 0.10:
            warnings.append(
                f"⚠️ Exposure alta: {exposure:.1%} del bankroll a rischio"
            )
        if ror > 0.05:
            warnings.append(
                f"⚠️ Risk of Ruin elevato: {ror:.1%} probabilità di bankrupt in 30gg"
            )
        if correlation > 0.5:
            warnings.append(
                f"⚠️ Correlazione rischio: {correlation:.0%} dei bets sulla stessa partita"
            )
        if status == "danger":
            warnings.append(
                "🛑 DANGER: considera di chiudere o ridimensionare bets aperti"
            )

        return warnings
