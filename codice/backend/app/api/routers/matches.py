from __future__ import annotations

import uuid
from datetime import datetime, date, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, and_, func, exists
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.models.match import Competition, Match, MatchOdds
from app.db.models.opportunity import BettingOpportunity

router = APIRouter(prefix="/matches", tags=["matches"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class CompetitionOut(BaseModel):
    id: uuid.UUID
    name: str
    sport: str
    tier: str
    weight: float

    model_config = {"from_attributes": True}


class OddsOut(BaseModel):
    bookmaker: str
    market: str
    outcome: str
    odds: float
    fetched_at: datetime
    is_live: bool

    model_config = {"from_attributes": True}


class MatchOut(BaseModel):
    id: uuid.UUID
    competition_id: uuid.UUID
    home_team: Optional[str]
    away_team: Optional[str]
    player_a: Optional[str]
    player_b: Optional[str]
    match_date: datetime
    sport: str
    status: str
    display_name: str
    has_value_bet: bool = False

    model_config = {"from_attributes": True}


class MatchDetailOut(MatchOut):
    competition: CompetitionOut
    best_odds: list[OddsOut]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=list[MatchOut])
async def list_matches(
    sport: Optional[str] = Query(None),
    status: Optional[str] = Query("scheduled"),
    only_today: bool = Query(True),
    only_value: bool = Query(False),
    limit: int = Query(100, le=300),
    db: AsyncSession = Depends(get_db),
):
    filters = []
    if sport:
        filters.append(Match.sport == sport)
    if status:
        filters.append(Match.status == status)
    if only_today:
        today = date.today()
        filters.append(func.date(Match.match_date) == today)

    # Subquery: match_id con almeno una opportunity pending o già confermata
    value_subq = (
        select(BettingOpportunity.match_id)
        .where(BettingOpportunity.status.in_(["pending", "bet_placed"]))
        .scalar_subquery()
    )

    if only_value:
        filters.append(Match.id.in_(value_subq))

    result = await db.execute(
        select(Match)
        .where(and_(*filters) if filters else True)
        .order_by(Match.match_date.asc())
        .limit(limit)
    )
    matches = result.scalars().all()

    # Carica set di match_id con value bet per flags
    value_result = await db.execute(
        select(BettingOpportunity.match_id)
        .where(
            BettingOpportunity.status.in_(["pending", "bet_placed"]),
            BettingOpportunity.match_id.in_([m.id for m in matches]),
        )
        .distinct()
    )
    value_ids = {row[0] for row in value_result.all()}

    return [_match_out(m, m.id in value_ids) for m in matches]


@router.get("/{match_id}", response_model=MatchDetailOut)
async def get_match(
    match_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Match)
        .options(selectinload(Match.competition))
        .where(Match.id == match_id)
    )
    match = result.scalar_one_or_none()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    # Load latest odds (deduplicated per bookmaker+market+outcome)
    odds_result = await db.execute(
        select(MatchOdds)
        .where(MatchOdds.match_id == match_id)
        .order_by(MatchOdds.fetched_at.desc())
        .limit(100)
    )
    all_odds = odds_result.scalars().all()
    seen: set[tuple] = set()
    best_odds = []
    for o in all_odds:
        key = (o.bookmaker, o.market, o.outcome)
        if key not in seen:
            seen.add(key)
            best_odds.append(o)

    out = _match_out(match)
    return {
        **out.model_dump(),
        "competition": match.competition,
        "best_odds": best_odds,
    }


@router.post("/{match_id}/analyse")
async def trigger_analysis(
    match_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger agent analysis for a specific match."""
    result = await db.execute(select(Match).where(Match.id == match_id))
    match = result.scalar_one_or_none()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    from app.workers.tasks import fetch_all_odds
    fetch_all_odds.delay()

    from app.agents.pipeline import analyse_match
    n = await analyse_match(match, db)
    return {"opportunities_found": n, "match": match.display_name()}


def _match_out(m: Match, has_value_bet: bool = False) -> MatchOut:
    return MatchOut(
        id=m.id,
        competition_id=m.competition_id,
        home_team=m.home_team,
        away_team=m.away_team,
        player_a=m.player_a,
        player_b=m.player_b,
        match_date=m.match_date,
        sport=m.sport,
        status=m.status,
        display_name=m.display_name(),
        has_value_bet=has_value_bet,
    )
