from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.models.bet import Bet

router = APIRouter(prefix="/bets", tags=["bets"])


class BetOut(BaseModel):
    id: uuid.UUID
    opportunity_id: uuid.UUID
    bookmaker: str
    market: str
    outcome: str
    odds: float
    stake: float
    status: str
    result: Optional[str]
    pnl: Optional[float]
    placed_at: datetime
    settled_at: Optional[datetime]

    model_config = {"from_attributes": True}


class BetStats(BaseModel):
    total_bets: int
    open_bets: int
    won: int
    lost: int
    total_staked: float
    total_pnl: float
    roi_pct: float
    win_rate: float


@router.get("", response_model=list[BetOut])
async def list_bets(
    status: Optional[str] = Query(None),
    limit: int = Query(50, le=500),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db),
):
    filters = [Bet.status == status] if status else []
    result = await db.execute(
        select(Bet)
        .where(and_(*filters) if filters else True)
        .order_by(Bet.placed_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()


@router.get("/stats", response_model=BetStats)
async def bet_stats(
    db: AsyncSession = Depends(get_db),
):
    """Aggregate P&L statistics across all bets."""
    total = await db.scalar(select(func.count(Bet.id))) or 0
    open_count = await db.scalar(
        select(func.count(Bet.id)).where(Bet.status == "open")
    ) or 0
    won = await db.scalar(
        select(func.count(Bet.id)).where(Bet.status == "won")
    ) or 0
    lost = await db.scalar(
        select(func.count(Bet.id)).where(Bet.status == "lost")
    ) or 0
    total_staked = await db.scalar(select(func.sum(Bet.stake))) or 0.0
    total_pnl = await db.scalar(
        select(func.sum(Bet.pnl)).where(Bet.pnl.isnot(None))
    ) or 0.0

    roi_pct = (float(total_pnl) / float(total_staked) * 100) if total_staked else 0.0
    settled = won + lost
    win_rate = (won / settled * 100) if settled else 0.0

    return BetStats(
        total_bets=total,
        open_bets=open_count,
        won=won,
        lost=lost,
        total_staked=float(total_staked),
        total_pnl=float(total_pnl),
        roi_pct=round(roi_pct, 2),
        win_rate=round(win_rate, 2),
    )


@router.get("/{bet_id}", response_model=BetOut)
async def get_bet(
    bet_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    from fastapi import HTTPException
    result = await db.execute(select(Bet).where(Bet.id == bet_id))
    bet = result.scalar_one_or_none()
    if not bet:
        raise HTTPException(status_code=404, detail="Bet not found")
    return bet
