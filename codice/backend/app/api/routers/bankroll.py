from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.models.bankroll import BankrollSnapshot
from app.db.models.runs import PipelineRun
from app.config import settings

router = APIRouter(prefix="/bankroll", tags=["bankroll"])


class SnapshotOut(BaseModel):
    id: str
    snapshot_date: datetime
    balance: float
    open_exposure: float
    daily_pnl: Optional[float]
    total_bets: int
    won_bets: int
    lost_bets: int
    roi_pct: Optional[float]

    model_config = {"from_attributes": True}


class BankrollStatus(BaseModel):
    current_balance: float
    initial_bankroll: float
    total_return_pct: float
    open_exposure: float
    available_to_bet: float
    last_snapshot: Optional[datetime]
    # Statistiche aggregate
    total_staked: float
    total_won: float
    total_pnl: float
    win_rate: float
    open_bets_count: int


class PipelineRunOut(BaseModel):
    id: str
    started_at: datetime
    finished_at: Optional[datetime]
    status: str
    matches_processed: int
    opportunities_found: int
    bets_placed: int
    error: Optional[str]

    model_config = {"from_attributes": True}


@router.get("/status", response_model=BankrollStatus)
async def bankroll_status(
    db: AsyncSession = Depends(get_db),
):
    from app.db.models.bet import Bet

    # Saldo attuale — ultimo snapshot
    last = await db.execute(
        select(BankrollSnapshot)
        .order_by(BankrollSnapshot.snapshot_date.desc())
        .limit(1)
    )
    snapshot = last.scalar_one_or_none()
    balance = float(snapshot.balance) if snapshot else settings.initial_bankroll
    exposure = float(snapshot.open_exposure) if snapshot else 0.0

    # Bankroll di partenza — ultimo reset point impostato dall'utente
    reset_result = await db.execute(
        select(BankrollSnapshot.balance)
        .where(BankrollSnapshot.is_reset_point == True)  # noqa: E712
        .order_by(BankrollSnapshot.snapshot_date.desc())
        .limit(1)
    )
    reset_balance = reset_result.scalar_one_or_none()
    initial = float(reset_balance) if reset_balance else settings.initial_bankroll

    total_return = (balance - initial) / initial * 100 if initial > 0 else 0.0
    daily_limit = balance * settings.max_daily_exposure_pct
    available = max(0.0, daily_limit - exposure)

    # Statistiche aggregate scommesse
    stats_result = await db.execute(
        select(
            func.coalesce(func.sum(Bet.stake), 0).label("total_staked"),
            func.coalesce(func.sum(Bet.pnl), 0).label("total_pnl"),
            func.count(Bet.id).filter(Bet.status == "won").label("won"),
            func.count(Bet.id).filter(Bet.status.in_(["won", "lost"])).label("settled"),
            func.count(Bet.id).filter(Bet.status == "open").label("open_bets"),
        )
    )
    stats = stats_result.one()
    total_staked = float(stats.total_staked)
    total_pnl = float(stats.total_pnl)
    total_won = total_staked + total_pnl  # quanto è rientrato in totale
    win_rate = (stats.won / stats.settled * 100) if stats.settled > 0 else 0.0

    return BankrollStatus(
        current_balance=balance,
        initial_bankroll=initial,
        total_return_pct=round(total_return, 2),
        open_exposure=exposure,
        available_to_bet=round(available, 2),
        last_snapshot=snapshot.snapshot_date if snapshot else None,
        total_staked=round(total_staked, 2),
        total_won=round(total_won, 2),
        total_pnl=round(total_pnl, 2),
        win_rate=round(win_rate, 1),
        open_bets_count=int(stats.open_bets),
    )


@router.get("/history", response_model=list[SnapshotOut])
async def bankroll_history(
    days: int = Query(30, le=365),
    db: AsyncSession = Depends(get_db),
):
    from datetime import timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.execute(
        select(BankrollSnapshot)
        .where(BankrollSnapshot.snapshot_date >= cutoff)
        .order_by(BankrollSnapshot.snapshot_date.asc())
    )
    rows = result.scalars().all()
    return [
        SnapshotOut(
            id=str(s.id),
            snapshot_date=s.snapshot_date,
            balance=float(s.balance),
            open_exposure=float(s.open_exposure),
            daily_pnl=float(s.daily_pnl) if s.daily_pnl is not None else None,
            total_bets=s.total_bets,
            won_bets=s.won_bets,
            lost_bets=s.lost_bets,
            roi_pct=float(s.roi_pct) if s.roi_pct is not None else None,
        )
        for s in rows
    ]


@router.get("/pipeline-runs", response_model=list[PipelineRunOut])
async def pipeline_runs(
    limit: int = Query(20, le=100),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PipelineRun)
        .order_by(PipelineRun.started_at.desc())
        .limit(limit)
    )
    runs = result.scalars().all()
    return [
        PipelineRunOut(
            id=str(r.id),
            started_at=r.started_at,
            finished_at=r.finished_at,
            status=r.status,
            matches_processed=r.matches_processed,
            opportunities_found=r.opportunities_found,
            bets_placed=r.bets_placed,
            error=r.error,
        )
        for r in runs
    ]
