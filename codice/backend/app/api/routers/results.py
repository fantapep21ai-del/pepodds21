"""Router per resoconto risultati scommesse — P&L, statistiche, winrate."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.models.bet import Bet

router = APIRouter(prefix="/results", tags=["results"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class BetResultOut(BaseModel):
    id: str
    market: str
    outcome: str
    stake: float
    odds: float
    pnl: Optional[float]
    status: str
    placed_at: datetime
    settled_at: Optional[datetime]


class SummaryOut(BaseModel):
    period_days: int
    total_bets: int
    total_staked: float
    total_pnl: float
    net_result: float
    win_rate: str
    wins: int
    losses: int
    roi: str


class BreakdownOut(BaseModel):
    singole: dict
    scalate: dict


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/summary", response_model=SummaryOut)
async def get_results_summary(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """Resoconto P&L ultimi N giorni."""
    start_date = datetime.now(timezone.utc) - timedelta(days=days)

    result = await db.execute(
        select(
            func.count(Bet.id).label("total_bets"),
            func.sum(Bet.stake).label("total_staked"),
            func.sum(Bet.pnl).label("total_pnl"),
            func.count(Bet.id).filter(Bet.status == "won").label("wins"),
        ).where(Bet.settled_at >= start_date)
    )

    row = result.first()
    total_bets = row.total_bets or 0
    total_staked = float(row.total_staked) if row.total_staked else 0.0
    total_pnl = float(row.total_pnl) if row.total_pnl else 0.0
    wins = row.wins or 0
    losses = total_bets - wins

    win_rate = f"{(wins / total_bets * 100):.1f}%" if total_bets > 0 else "0%"
    roi = f"{(total_pnl / total_staked * 100):.1f}%" if total_staked > 0 else "0%"

    return SummaryOut(
        period_days=days,
        total_bets=total_bets,
        total_staked=total_staked,
        total_pnl=total_pnl,
        net_result=total_pnl,
        win_rate=win_rate,
        wins=wins,
        losses=losses,
        roi=roi,
    )


@router.get("/breakdown", response_model=BreakdownOut)
async def get_results_breakdown(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """Resoconto separato: singole vs scalate."""
    start_date = datetime.now(timezone.utc) - timedelta(days=days)

    # Singole (bet senza scalata_id)
    singole_result = await db.execute(
        select(
            func.count(Bet.id),
            func.sum(Bet.stake),
            func.sum(Bet.pnl),
            func.count(Bet.id).filter(Bet.status == "won"),
        ).where(
            Bet.settled_at >= start_date,
            Bet.scalata_id.is_(None),
        )
    )
    s = singole_result.first()

    # Scalate (bet con scalata_id)
    scalate_result = await db.execute(
        select(
            func.count(Bet.id),
            func.sum(Bet.stake),
            func.sum(Bet.pnl),
            func.count(Bet.id).filter(Bet.status == "won"),
        ).where(
            Bet.settled_at >= start_date,
            Bet.scalata_id.isnot(None),
        )
    )
    sc = scalate_result.first()

    def _format(count, staked, pnl, wins):
        return {
            "count": count or 0,
            "staked": float(staked) if staked else 0.0,
            "pnl": float(pnl) if pnl else 0.0,
            "wins": wins or 0,
            "win_rate": f"{(wins / count * 100):.1f}%" if count else "0%",
        }

    return BreakdownOut(
        singole=_format(s[0], s[1], s[2], s[3]),
        scalate=_format(sc[0], sc[1], sc[2], sc[3]),
    )


@router.get("/recent", response_model=list[BetResultOut])
async def get_recent_results(
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """Ultimi N risultati liquidati."""
    result = await db.execute(
        select(Bet)
        .where(Bet.status.in_(["won", "lost"]))
        .order_by(Bet.settled_at.desc())
        .limit(limit)
    )
    bets = result.scalars().all()

    return [
        BetResultOut(
            id=str(b.id),
            market=b.market,
            outcome=b.outcome,
            stake=float(b.stake),
            odds=float(b.odds),
            pnl=float(b.pnl) if b.pnl else None,
            status=b.status,
            placed_at=b.placed_at,
            settled_at=b.settled_at,
        )
        for b in bets
    ]
