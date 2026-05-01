from __future__ import annotations

import uuid
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.models.opportunity import BettingOpportunity
from app.db.models.match import Match

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/opportunities", tags=["opportunities"])


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class ApproveRequest(BaseModel):
    stake_override: Optional[float] = None
    bet_type_override: Optional[str] = None   # singola only


class ModifyRequest(BaseModel):
    stake: Optional[float] = None
    bet_type: Optional[str] = None
    notes: Optional[str] = None


class OpportunityOut(BaseModel):
    id: uuid.UUID
    match_id: uuid.UUID
    market: str
    outcome: str
    bookmaker: str
    best_odds: float
    model_probability: float
    expected_value: float
    uncertainty_score: float
    bet_type: str = "singola"
    confidence_level: str = "normale"
    tier: Optional[str] = "C"
    edge: Optional[float] = None
    status: str
    rejection_reason: Optional[str]
    uncertainty_blocked: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _get_match_name(opp: BettingOpportunity, db: AsyncSession) -> str:
    try:
        m = (await db.execute(select(Match).where(Match.id == opp.match_id))).scalar_one_or_none()
        return m.display_name() if m else "—"
    except Exception:
        return "—"


# ─────────────────────────────────────────────────────────────────────────────
# Read endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[OpportunityOut])
async def list_opportunities(
    status: Optional[str] = Query("pending"),
    match_id: Optional[uuid.UUID] = Query(None),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
):
    filters = []
    if status:
        filters.append(BettingOpportunity.status == status)
    if match_id:
        filters.append(BettingOpportunity.match_id == match_id)

    result = await db.execute(
        select(BettingOpportunity)
        .where(and_(*filters) if filters else True)
        .order_by(BettingOpportunity.expected_value.desc())
        .limit(limit)
    )
    return result.scalars().all()


@router.get("/{opp_id}", response_model=OpportunityOut)
async def get_opportunity(
    opp_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    opp = (await db.execute(
        select(BettingOpportunity).where(BettingOpportunity.id == opp_id)
    )).scalar_one_or_none()
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return opp


# ─────────────────────────────────────────────────────────────────────────────
# Action endpoints (write) — ogni azione notifica Telegram per sincronizzazione
# ─────────────────────────────────────────────────────────────────────────────

@router.patch("/{opp_id}/modifica")
async def modifica_opportunity(
    opp_id: uuid.UUID,
    body: ModifyRequest,
    db: AsyncSession = Depends(get_db),
):
    """Modifica importo e/o tipo prima di confermare. Notifica Telegram."""
    opp = (await db.execute(
        select(BettingOpportunity).where(BettingOpportunity.id == opp_id)
    )).scalar_one_or_none()
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    if opp.status not in ("pending", "in_attesa"):
        raise HTTPException(status_code=400, detail=f"Non modificabile — status: {opp.status}")

    if body.bet_type is not None:
        opp.bet_type = body.bet_type
    if body.notes is not None:
        opp.rejection_reason = body.notes

    await db.commit()

    # Sync → Telegram
    try:
        from app.services.telegram_service import notify_opportunity_modified
        match_name = await _get_match_name(opp, db)
        await notify_opportunity_modified(opp, match_name)
    except Exception as exc:
        logger.warning("Telegram sync (modify) failed: %s", exc)

    return {
        "id": str(opp.id),
        "bet_type": opp.bet_type,
        "status": opp.status,
    }


@router.post("/{opp_id}/attesa")
async def metti_in_attesa(
    opp_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    Mette una opportunità in attesa per combinarla con un'altra.
    Auto-scadenza: se la partita inizia prima che venga giocata, viene rifiutata.
    """
    opp = (await db.execute(
        select(BettingOpportunity).where(BettingOpportunity.id == opp_id)
    )).scalar_one_or_none()
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    if opp.status != "pending":
        raise HTTPException(status_code=400, detail=f"Status attuale: {opp.status}")

    opp.status = "in_attesa"
    await db.commit()

    # Sync → Telegram
    try:
        from app.services.telegram_service import notify_opportunity_on_hold
        match_name = await _get_match_name(opp, db)
        await notify_opportunity_on_hold(opp, match_name)
    except Exception as exc:
        logger.warning("Telegram sync (hold) failed: %s", exc)

    return {"status": "in_attesa", "id": str(opp.id)}


@router.post("/{opp_id}/approve")
async def approve_opportunity(
    opp_id: uuid.UUID,
    body: ApproveRequest,
    db: AsyncSession = Depends(get_db),
):
    """Conferma un'opportunità e crea la scommessa. Notifica Telegram."""
    opp = (await db.execute(
        select(BettingOpportunity).where(BettingOpportunity.id == opp_id)
    )).scalar_one_or_none()
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    if opp.status not in ("pending", "in_attesa"):
        raise HTTPException(status_code=400, detail=f"Opportunity status: '{opp.status}'")
    if opp.uncertainty_blocked:
        raise HTTPException(status_code=400, detail="Bloccata dall'uncertainty gate")

    # Controlla che la partita non sia già iniziata
    match_check = (await db.execute(
        select(Match).where(Match.id == opp.match_id)
    )).scalar_one_or_none()
    if match_check and match_check.match_date:
        from datetime import datetime, timezone
        if match_check.match_date <= datetime.now(timezone.utc):
            opp.status = "expired"
            opp.rejection_reason = "Partita già iniziata al momento dell'approvazione"
            await db.commit()
            raise HTTPException(status_code=400, detail="Partita già iniziata — opportunità scaduta")

    stake = body.stake_override or 0.0
    if stake <= 0:
        raise HTTPException(status_code=400, detail="Importo non valido — specifica stake_override")

    if body.bet_type_override:
        opp.bet_type = body.bet_type_override

    from app.db.models.bet import Bet
    bet = Bet(
        opportunity_id=opp.id,
        bookmaker=opp.bookmaker,
        market=opp.market,
        outcome=opp.outcome,
        odds=opp.best_odds,
        stake=stake,
        status="open",
    )
    opp.status = "bet_placed"
    db.add(bet)
    await db.commit()
    await db.refresh(bet)

    # Sync → Telegram
    try:
        from app.services.telegram_service import notify_opportunity_confirmed
        match_name = await _get_match_name(opp, db)
        await notify_opportunity_confirmed(opp, match_name, stake)
    except Exception as exc:
        logger.warning("Telegram sync (approve) failed: %s", exc)

    return {
        "bet_id": str(bet.id),
        "stake": float(bet.stake),
        "odds": float(bet.odds),
        "market": bet.market,
        "outcome": bet.outcome,
        "bet_type": opp.bet_type,
    }


@router.post("/{opp_id}/reject")
async def reject_opportunity(
    opp_id: uuid.UUID,
    reason: str = Query("Rifiutata manualmente"),
    db: AsyncSession = Depends(get_db),
):
    """Rifiuta un'opportunità. Notifica Telegram."""
    opp = (await db.execute(
        select(BettingOpportunity).where(BettingOpportunity.id == opp_id)
    )).scalar_one_or_none()
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    opp.status = "rejected"
    opp.rejection_reason = reason
    await db.commit()

    # Sync → Telegram
    try:
        from app.services.telegram_service import notify_opportunity_rejected
        match_name = await _get_match_name(opp, db)
        await notify_opportunity_rejected(opp, match_name, reason)
    except Exception as exc:
        logger.warning("Telegram sync (reject) failed: %s", exc)

    return {"status": "rejected"}
