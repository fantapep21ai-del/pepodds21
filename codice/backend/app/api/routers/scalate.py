"""Router per le scalate — sequenze di scommesse all-in."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.db.models.scalata import Scalata, ScalataStep
from app.db.models.opportunity import BettingOpportunity
from app.db.models.bet import Bet

router = APIRouter(prefix="/scalate", tags=["scalate"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class StepOut(BaseModel):
    id: str
    step_number: int
    status: str
    odds: float
    stake: float
    match_name: str
    market: str
    outcome: str
    bookmaker: str
    match_date: Optional[datetime]
    placed_at: Optional[datetime]
    settled_at: Optional[datetime]
    opportunity_id: Optional[str]
    bet_id: Optional[str]


class ScalataOut(BaseModel):
    id: str
    status: str
    total_steps: int
    current_step: int
    start_amount: float
    current_amount: float
    potential_win: Optional[float]
    created_at: datetime
    completed_at: Optional[datetime]
    total_pnl: Optional[float]
    notes: Optional[str]
    steps: list[StepOut]


class ConfirmStepBody(BaseModel):
    start_amount: float  # quanto vuole puntare per iniziare (o alla conferma del passo)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=list[ScalataOut])
async def list_scalate(
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Scalata)
        .options(selectinload(Scalata.steps))
        .order_by(Scalata.created_at.desc())
        .limit(50)
    )
    scalate = result.scalars().all()
    return [_serialize(s) for s in scalate]


@router.get("/{scalata_id}", response_model=ScalataOut)
async def get_scalata(
    scalata_id: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Scalata)
        .options(selectinload(Scalata.steps))
        .where(Scalata.id == scalata_id)
    )
    scalata = result.scalar_one_or_none()
    if not scalata:
        raise HTTPException(status_code=404, detail="Scalata non trovata")
    return _serialize(scalata)


@router.post("/{scalata_id}/conferma", response_model=ScalataOut)
async def conferma_step(
    scalata_id: str,
    body: ConfirmStepBody,
    db: AsyncSession = Depends(get_db),
):
    """
    Conferma il prossimo step della scalata.
    Se current_step == 0 → inizia la scalata con start_amount fornito.
    Se current_step > 0 → conferma il prossimo step (stake = vincita precedente).
    Crea un Bet nel DB e segna lo step come 'attivo'.
    """
    result = await db.execute(
        select(Scalata)
        .options(selectinload(Scalata.steps))
        .where(Scalata.id == scalata_id)
    )
    scalata = result.scalar_one_or_none()
    if not scalata:
        raise HTTPException(status_code=404, detail="Scalata non trovata")
    if scalata.status != "attiva":
        raise HTTPException(status_code=400, detail=f"Scalata non attiva (status: {scalata.status})")

    # Trova il prossimo step da confermare
    next_step_num = scalata.current_step + 1
    step = next((s for s in scalata.steps if s.step_number == next_step_num), None)
    if not step:
        raise HTTPException(status_code=400, detail="Nessun step disponibile")

    # Calcola stake
    if scalata.current_step == 0:
        # Primo step: usa start_amount fornito dall'utente
        stake = body.start_amount
        scalata.start_amount = stake
        scalata.current_amount = stake
        scalata.started_at = datetime.now(timezone.utc)
    else:
        # Step successivi: all-in (stake = current_amount già aggiornato)
        stake = float(scalata.current_amount)

    step.stake = stake
    step.status = "attivo"
    step.placed_at = datetime.now(timezone.utc)
    scalata.current_step = next_step_num

    # Crea il Bet nel DB se c'è un'opportunity linkata
    if step.opportunity_id:
        opp_result = await db.execute(
            select(BettingOpportunity).where(BettingOpportunity.id == step.opportunity_id)
        )
        opp = opp_result.scalar_one_or_none()
        if opp and opp.status == "pending":
            bet = Bet(
                opportunity_id=opp.id,
                bookmaker=step.bookmaker,
                market=step.market,
                outcome=step.outcome,
                odds=step.odds,
                stake=stake,
                status="open",
                placed_at=datetime.now(timezone.utc),
            )
            db.add(bet)
            await db.flush()
            step.bet_id = bet.id
            opp.status = "bet_placed"

    await db.commit()
    await db.refresh(scalata)

    return _serialize(scalata)


@router.post("/{scalata_id}/step/{step_number}/risultato")
async def registra_risultato(
    scalata_id: str,
    step_number: int,
    won: bool,
    db: AsyncSession = Depends(get_db),
):
    """
    Registra manualmente il risultato di uno step (vinto/perso).
    Normalmente gestito automaticamente dal settlement service.
    """
    result = await db.execute(
        select(Scalata)
        .options(selectinload(Scalata.steps))
        .where(Scalata.id == scalata_id)
    )
    scalata = result.scalar_one_or_none()
    if not scalata:
        raise HTTPException(status_code=404, detail="Scalata non trovata")

    step = next((s for s in scalata.steps if s.step_number == step_number), None)
    if not step:
        raise HTTPException(status_code=404, detail="Step non trovato")

    now = datetime.now(timezone.utc)
    step.settled_at = now

    if won:
        step.status = "vinto"
        winnings = round(float(step.stake) * float(step.odds), 2)
        scalata.current_amount = winnings

        if step_number >= scalata.total_steps:
            # Scalata completata!
            scalata.status = "vinta"
            scalata.completed_at = now
            scalata.total_pnl = round(winnings - float(scalata.start_amount), 2)
        else:
            # Avanza al prossimo step
            next_step = next((s for s in scalata.steps if s.step_number == step_number + 1), None)
            if next_step:
                next_step.status = "attivo"
    else:
        step.status = "perso"
        scalata.status = "persa"
        scalata.completed_at = now
        scalata.total_pnl = -float(step.stake)

    await db.commit()

    # Notifica Telegram
    try:
        from app.services.telegram_service import send_scalata_step_result
        await send_scalata_step_result(scalata, step, won)
    except Exception:
        pass

    return {"ok": True, "scalata_status": scalata.status}


# ── Stats ─────────────────────────────────────────────────────────────────────

class ScalataStats(BaseModel):
    totale: int
    vinte: int
    perse: int
    attive: int
    success_rate: float
    profitto_totale: float
    profitto_medio: float


@router.get("/stats/riepilogo", response_model=ScalataStats)
async def scalata_stats(
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Scalata))
    scalate = result.scalars().all()

    totale = len(scalate)
    vinte = sum(1 for s in scalate if s.status == "vinta")
    perse = sum(1 for s in scalate if s.status == "persa")
    attive = sum(1 for s in scalate if s.status == "attiva")
    chiuse = vinte + perse
    success_rate = (vinte / chiuse * 100) if chiuse > 0 else 0.0
    pnl_list = [float(s.total_pnl) for s in scalate if s.total_pnl is not None]
    profitto_totale = sum(pnl_list)
    profitto_medio = (profitto_totale / len(pnl_list)) if pnl_list else 0.0

    return ScalataStats(
        totale=totale,
        vinte=vinte,
        perse=perse,
        attive=attive,
        success_rate=round(success_rate, 1),
        profitto_totale=round(profitto_totale, 2),
        profitto_medio=round(profitto_medio, 2),
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _serialize(s: Scalata) -> ScalataOut:
    return ScalataOut(
        id=str(s.id),
        status=s.status,
        total_steps=s.total_steps,
        current_step=s.current_step,
        start_amount=float(s.start_amount),
        current_amount=float(s.current_amount),
        potential_win=float(s.potential_win) if s.potential_win else None,
        created_at=s.created_at,
        completed_at=s.completed_at,
        total_pnl=float(s.total_pnl) if s.total_pnl is not None else None,
        notes=s.notes,
        steps=[
            StepOut(
                id=str(st.id),
                step_number=st.step_number,
                status=st.status,
                odds=float(st.odds),
                stake=float(st.stake),
                match_name=st.match_name,
                market=st.market,
                outcome=st.outcome,
                bookmaker=st.bookmaker,
                match_date=st.match_date,
                placed_at=st.placed_at,
                settled_at=st.settled_at,
                opportunity_id=str(st.opportunity_id) if st.opportunity_id else None,
                bet_id=str(st.bet_id) if st.bet_id else None,
            )
            for st in s.steps
        ],
    )
