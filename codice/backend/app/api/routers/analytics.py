"""
Analytics API — performance statistics and CLV tracking.

GET /analytics/performance  — overall system performance (ROI, win rate, tier breakdown)
GET /analytics/clv          — Closing Line Value analysis (quality indicator)
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.db.models.user import User

router = APIRouter(prefix="/analytics", tags=["analytics"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class TierPerformance(BaseModel):
    tier: str
    total_bets: int
    won: int
    win_rate: float
    total_staked: float
    total_pnl: float
    roi_pct: float
    avg_ev: float


class PerformanceOut(BaseModel):
    period_days: int
    total_bets: int
    won: int
    lost: int
    win_rate: float
    total_staked: float
    total_pnl: float
    roi_pct: float
    avg_odds: float
    no_bet_days: int  # days with no qualifying bets
    by_tier: list[TierPerformance]
    by_bet_type: dict[str, dict]


class CLVRecord(BaseModel):
    bet_id: str
    match_name: Optional[str]
    placed_odds: float
    closing_odds: Optional[float]
    clv: Optional[float]          # (closing/placed - 1) * 100
    ev_at_placement: float
    tier: Optional[str]
    placed_at: datetime


class CLVSummaryOut(BaseModel):
    avg_clv: Optional[float]
    positive_clv_pct: float       # % of bets where CLV > 0 (beating closing line)
    total_bets_with_clv: int
    records: list[CLVRecord]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/performance", response_model=PerformanceOut)
async def get_performance(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """System performance breakdown by tier and bet type."""
    from app.db.models.bet import Bet
    from app.db.models.opportunity import BettingOpportunity
    from app.db.models.match import Match

    since = datetime.now(timezone.utc) - timedelta(days=days)

    # All settled bets in period
    result = await db.execute(
        select(Bet, BettingOpportunity)
        .join(BettingOpportunity, BettingOpportunity.id == Bet.opportunity_id)
        .where(
            and_(
                Bet.placed_at >= since,
                Bet.status.in_(["won", "lost"]),
            )
        )
    )
    rows = result.all()

    total = len(rows)
    won_count = sum(1 for b, _ in rows if b.status == "won")
    total_staked = sum(float(b.stake) for b, _ in rows)
    total_pnl = sum(float(b.pnl or 0) for b, _ in rows)
    avg_odds = (sum(float(b.odds) for b, _ in rows) / total) if total > 0 else 0.0

    # By tier
    tier_map: dict[str, dict] = {}
    for bet, opp in rows:
        t = opp.tier if opp else "?"
        if t not in tier_map:
            tier_map[t] = {"total": 0, "won": 0, "staked": 0.0, "pnl": 0.0, "ev_sum": 0.0}
        tier_map[t]["total"] += 1
        tier_map[t]["staked"] += float(bet.stake)
        tier_map[t]["pnl"] += float(bet.pnl or 0)
        tier_map[t]["ev_sum"] += float(opp.expected_value) if opp else 0.0
        if bet.status == "won":
            tier_map[t]["won"] += 1

    by_tier = []
    for tier, d in sorted(tier_map.items()):
        wr = d["won"] / max(d["total"], 1)
        roi = (d["pnl"] / max(d["staked"], 0.01)) * 100
        by_tier.append(TierPerformance(
            tier=tier,
            total_bets=d["total"],
            won=d["won"],
            win_rate=round(wr, 4),
            total_staked=round(d["staked"], 2),
            total_pnl=round(d["pnl"], 2),
            roi_pct=round(roi, 2),
            avg_ev=round(d["ev_sum"] / max(d["total"], 1), 4),
        ))

    # By bet type
    type_map: dict[str, dict] = {}
    for bet, opp in rows:
        bt = opp.bet_type if opp else "singola"
        if bt not in type_map:
            type_map[bt] = {"total": 0, "won": 0, "pnl": 0.0}
        type_map[bt]["total"] += 1
        type_map[bt]["pnl"] += float(bet.pnl or 0)
        if bet.status == "won":
            type_map[bt]["won"] += 1

    by_bet_type = {
        bt: {
            "total": d["total"],
            "won": d["won"],
            "win_rate": round(d["won"] / max(d["total"], 1), 4),
            "pnl": round(d["pnl"], 2),
        }
        for bt, d in type_map.items()
    }

    return PerformanceOut(
        period_days=days,
        total_bets=total,
        won=won_count,
        lost=total - won_count,
        win_rate=round(won_count / max(total, 1), 4),
        total_staked=round(total_staked, 2),
        total_pnl=round(total_pnl, 2),
        roi_pct=round((total_pnl / max(total_staked, 0.01)) * 100, 2),
        avg_odds=round(avg_odds, 3),
        no_bet_days=0,  # TODO: count pipeline runs with 0 qualifying bets
        by_tier=by_tier,
        by_bet_type=by_bet_type,
    )


@router.get("/clv", response_model=CLVSummaryOut)
async def get_clv(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """
    Closing Line Value analysis.
    CLV > 0 means we consistently bet before the market narrows — strong long-term signal.
    """
    from app.db.models.bet import Bet
    from app.db.models.opportunity import BettingOpportunity
    from app.db.models.match import Match

    since = datetime.now(timezone.utc) - timedelta(days=days)

    result = await db.execute(
        select(Bet, BettingOpportunity, Match)
        .join(BettingOpportunity, BettingOpportunity.id == Bet.opportunity_id)
        .join(Match, Match.id == BettingOpportunity.match_id)
        .where(Bet.placed_at >= since)
        .order_by(Bet.placed_at.desc())
        .limit(200)
    )
    rows = result.all()

    bets_with_clv = [r for r in rows if r[0].clv is not None]
    clv_values = [float(r[0].clv) for r in bets_with_clv]
    avg_clv = round(sum(clv_values) / len(clv_values), 4) if clv_values else None
    positive_clv_pct = round(
        sum(1 for v in clv_values if v > 0) / max(len(clv_values), 1), 4
    )

    records = [
        CLVRecord(
            bet_id=str(bet.id),
            match_name=match.display_name() if match else None,
            placed_odds=float(bet.odds),
            closing_odds=float(bet.closing_odds) if bet.closing_odds else None,
            clv=float(bet.clv) if bet.clv else None,
            ev_at_placement=float(opp.expected_value) if opp else 0.0,
            tier=opp.tier if opp else None,
            placed_at=bet.placed_at,
        )
        for bet, opp, match in rows
    ]

    return CLVSummaryOut(
        avg_clv=avg_clv,
        positive_clv_pct=positive_clv_pct,
        total_bets_with_clv=len(bets_with_clv),
        records=records,
    )
