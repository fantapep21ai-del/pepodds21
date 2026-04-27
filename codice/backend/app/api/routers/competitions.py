from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_admin
from app.db.session import get_db
from app.db.models.match import Competition
from app.db.models.user import User

router = APIRouter(prefix="/competitions", tags=["competitions"])


class CompetitionOut(BaseModel):
    id: uuid.UUID
    name: str
    sport: str
    tier: str
    weight: float
    external_id: Optional[str]
    odds_api_key: Optional[str]

    model_config = {"from_attributes": True}


class CompetitionCreate(BaseModel):
    name: str
    sport: str
    tier: str = "standard"
    weight: float = 1.0
    external_id: Optional[str] = None
    odds_api_key: Optional[str] = None


@router.get("", response_model=list[CompetitionOut])
async def list_competitions(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(Competition).order_by(Competition.sport, Competition.name))
    return result.scalars().all()


@router.post("", response_model=CompetitionOut)
async def create_competition(
    body: CompetitionCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    comp = Competition(**body.model_dump())
    db.add(comp)
    await db.commit()
    await db.refresh(comp)
    return comp


@router.delete("/{comp_id}", status_code=204)
async def delete_competition(
    comp_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(Competition).where(Competition.id == comp_id))
    comp = result.scalar_one_or_none()
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")
    await db.delete(comp)
    await db.commit()
