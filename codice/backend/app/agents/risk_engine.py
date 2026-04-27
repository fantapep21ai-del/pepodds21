"""
Risk engine — sizes bets using fractional Kelly criterion
and enforces exposure limits.

Kelly formula:
  f* = (b × p - q) / b
  where:
    b = decimal_odds - 1  (net return per unit)
    p = model probability (win)
    q = 1 - p             (loss)
    f* = fraction of bankroll to bet

We apply a Kelly multiplier (default 0.25 = quarter-Kelly) to reduce variance.
Additional hard limits:
  - Max single bet: max_single_bet_pct of bankroll
  - Max daily exposure: max_daily_exposure_pct of bankroll
  - Min stake: €2 (below this, skip)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import settings

logger = logging.getLogger(__name__)

MIN_STAKE_EUR = 2.0
MAX_SINGLE_BET_PCT = 0.03      # hard cap: 3% of bankroll per bet (spec requirement)
DEFAULT_KELLY_MULT = settings.kelly_multiplier   # 0.25 = quarter-Kelly
MAX_DAILY_EXPOSURE_PCT = settings.max_daily_exposure_pct
DAILY_DRAWDOWN_STOP_PCT = 0.08  # if daily loss ≥ 8% → READONLY mode


@dataclass
class SizingResult:
    stake: float           # euros to bet
    kelly_fraction: float  # raw kelly fraction (before multiplier)
    adjusted_fraction: float  # after multiplier + caps
    rejected: bool = False
    rejection_reason: str = ""


def compute_kelly_stake(
    model_probability: float,
    decimal_odds: float,
    bankroll: float,
    daily_exposure_used: float = 0.0,
    kelly_multiplier: float = DEFAULT_KELLY_MULT,
) -> SizingResult:
    """
    Calculate the optimal bet size for a single opportunity.

    Args:
        model_probability: consensus model probability of winning
        decimal_odds:       best available decimal odds
        bankroll:           current bankroll in euros
        daily_exposure_used: euros already at risk today (open bets)
        kelly_multiplier:   fraction of Kelly to use (0.25 = quarter-Kelly)

    Returns:
        SizingResult with stake and rejection reason if applicable
    """
    b = decimal_odds - 1.0  # net odds
    p = model_probability
    q = 1.0 - p

    # Kelly fraction
    if b <= 0:
        return SizingResult(
            stake=0, kelly_fraction=0, adjusted_fraction=0,
            rejected=True, rejection_reason="Invalid odds (b <= 0)",
        )

    kelly_f = (b * p - q) / b

    if kelly_f <= 0:
        return SizingResult(
            stake=0, kelly_fraction=kelly_f, adjusted_fraction=0,
            rejected=True, rejection_reason=f"Negative Kelly ({kelly_f:.4f}) — no edge",
        )

    # Apply multiplier
    adjusted_f = kelly_f * kelly_multiplier

    # Cap at max single bet
    adjusted_f = min(adjusted_f, MAX_SINGLE_BET_PCT)

    stake = bankroll * adjusted_f

    # Check daily exposure limit
    daily_limit = bankroll * MAX_DAILY_EXPOSURE_PCT
    remaining_daily = daily_limit - daily_exposure_used

    if remaining_daily <= 0:
        return SizingResult(
            stake=0, kelly_fraction=kelly_f, adjusted_fraction=adjusted_f,
            rejected=True, rejection_reason="Daily exposure limit reached",
        )

    stake = min(stake, remaining_daily)

    if stake < MIN_STAKE_EUR:
        return SizingResult(
            stake=0, kelly_fraction=kelly_f, adjusted_fraction=adjusted_f,
            rejected=True, rejection_reason=f"Stake too small (€{stake:.2f} < €{MIN_STAKE_EUR})",
        )

    logger.debug(
        "Kelly sizing: p=%.3f odds=%.2f b=%.2f → raw_f=%.4f adj_f=%.4f stake=€%.2f",
        p, decimal_odds, b, kelly_f, adjusted_f, stake,
    )

    return SizingResult(
        stake=round(stake, 2),
        kelly_fraction=kelly_f,
        adjusted_fraction=adjusted_f,
    )


def compute_implied_probability(decimal_odds: float) -> float:
    """Convert decimal odds to implied probability (no vig removal)."""
    if decimal_odds <= 1.0:
        return 1.0
    return 1.0 / decimal_odds


def remove_vig(odds_list: list[float]) -> list[float]:
    """
    Remove bookmaker margin from a list of odds for the same market.
    Returns fair probabilities that sum to 1.0.
    """
    implied = [1.0 / o for o in odds_list if o > 1.0]
    total = sum(implied)
    if total == 0:
        return [1.0 / len(odds_list)] * len(odds_list)
    return [p / total for p in implied]


def ev_from_fair_odds(model_prob: float, decimal_odds: float) -> float:
    """Expected value: positive = value bet."""
    return (model_prob * decimal_odds) - 1.0


async def check_daily_drawdown(db) -> tuple[float, bool]:
    """
    Compute today's drawdown and return (drawdown_pct, readonly_mode).
    readonly_mode = True when drawdown >= DAILY_DRAWDOWN_STOP_PCT.

    Also persists a SystemHealth record.
    """
    from sqlalchemy import select, func
    from app.db.models.bet import Bet
    from app.db.models.bankroll import BankrollSnapshot
    from app.db.models.context import SystemHealth
    from datetime import datetime, timezone, date

    today = date.today()

    # Today's settled P&L
    daily_pnl = await db.scalar(
        select(func.sum(Bet.pnl))
        .where(func.date(Bet.settled_at) == today)
        .where(Bet.status.in_(["won", "lost"]))
    ) or 0.0
    daily_pnl = float(daily_pnl)

    # Starting bankroll (last snapshot before today or initial)
    start_balance = await db.scalar(
        select(BankrollSnapshot.balance)
        .where(func.date(BankrollSnapshot.snapshot_date) < today)
        .order_by(BankrollSnapshot.snapshot_date.desc())
        .limit(1)
    )
    start_balance = float(start_balance or settings.initial_bankroll)

    drawdown_pct = abs(min(daily_pnl, 0.0)) / max(start_balance, 1.0)
    readonly_mode = drawdown_pct >= DAILY_DRAWDOWN_STOP_PCT

    # Write health record
    health = SystemHealth(
        checked_at=datetime.now(timezone.utc),
        status="healthy" if not readonly_mode else "degraded",
        readonly_mode=readonly_mode,
        drawdown_pct=drawdown_pct,
        services={"drawdown_check": "ok"},
        notes=f"Daily P&L: €{daily_pnl:.2f} / Drawdown: {drawdown_pct:.2%}",
    )
    db.add(health)
    await db.commit()

    if readonly_mode:
        logger.warning(
            "READONLY MODE ACTIVATED — daily drawdown %.2f%% ≥ %.2f%%",
            drawdown_pct * 100, DAILY_DRAWDOWN_STOP_PCT * 100,
        )

    return drawdown_pct, readonly_mode


async def is_readonly_mode(db) -> bool:
    """Check latest health record for readonly status."""
    from sqlalchemy import select
    from app.db.models.context import SystemHealth

    latest = await db.scalar(
        select(SystemHealth.readonly_mode)
        .order_by(SystemHealth.checked_at.desc())
        .limit(1)
    )
    return bool(latest)
