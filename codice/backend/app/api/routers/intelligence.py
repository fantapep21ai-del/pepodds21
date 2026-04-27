"""
Intelligence API — provides enriched data for a match, player, or market.

GET /intelligence/match/{match_id}    — full context for a match
GET /intelligence/player/{player_id}  — player stats + form
GET /intelligence/news                — latest relevant news
GET /intelligence/market/{match_id}   — market signals + odds movement
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db

router = APIRouter(prefix="/intelligence", tags=["intelligence"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class MarketSignalsOut(BaseModel):
    opening_odds: Optional[float] = None
    current_odds: Optional[float] = None
    line_movement: Optional[float] = None
    sharp_side: Optional[str] = None
    public_pct: Optional[float] = None
    sharp_pct: Optional[float] = None


class MatchIntelligenceOut(BaseModel):
    match_id: str
    display_name: str
    match_date: datetime
    lineups: Optional[dict] = None
    injuries: Optional[dict] = None
    weather: Optional[dict] = None
    home_form: Optional[list] = None
    away_form: Optional[list] = None
    h2h: Optional[dict] = None
    market_signals: Optional[dict] = None
    momentum_score: Optional[float] = None
    opportunities_count: int = 0
    best_tier: Optional[str] = None


class PlayerIntelligenceOut(BaseModel):
    player_id: str
    name: str
    sport: str
    team: Optional[str] = None
    position: Optional[str] = None
    latest_stats: Optional[dict] = None
    stats_source: Optional[str] = None
    stats_date: Optional[datetime] = None


class NewsOut(BaseModel):
    id: str
    title: str
    source: Optional[str] = None
    sentiment: Optional[float] = None
    relevance: Optional[float] = None
    published_at: Optional[datetime] = None
    team: Optional[str] = None
    match_id: Optional[str] = None


class MarketIntelligenceOut(BaseModel):
    match_id: str
    display_name: str
    odds_history: list[dict]
    market_signals: Optional[dict] = None
    best_available: list[dict]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/match/{match_id}", response_model=MatchIntelligenceOut)
async def get_match_intelligence(
    match_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Full intelligence summary for a match — context, market, opportunities."""
    from app.db.models.match import Match, MatchOdds
    from app.db.models.context import MatchContext
    from app.db.models.opportunity import BettingOpportunity
    from sqlalchemy import func

    result = await db.execute(select(Match).where(Match.id == match_id))
    match = result.scalar_one_or_none()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    # Load context
    ctx_result = await db.execute(
        select(MatchContext).where(MatchContext.match_id == match_id)
    )
    ctx = ctx_result.scalar_one_or_none()

    # Count opportunities and find best tier
    opps_result = await db.execute(
        select(BettingOpportunity)
        .where(
            and_(
                BettingOpportunity.match_id == match_id,
                BettingOpportunity.status.in_(["pending", "approved", "bet_placed"]),
            )
        )
        .order_by(BettingOpportunity.edge.desc().nullslast())
    )
    opps = opps_result.scalars().all()
    best_tier = opps[0].tier if opps else None

    return MatchIntelligenceOut(
        match_id=str(match_id),
        display_name=match.display_name(),
        match_date=match.match_date,
        lineups=ctx.lineups if ctx else None,
        injuries=ctx.injuries if ctx else None,
        weather=ctx.weather if ctx else None,
        home_form=ctx.home_form if ctx else None,
        away_form=ctx.away_form if ctx else None,
        h2h=ctx.h2h if ctx else None,
        market_signals=ctx.market_signals if ctx else None,
        momentum_score=float(ctx.momentum_score) if ctx and ctx.momentum_score else None,
        opportunities_count=len(opps),
        best_tier=best_tier,
    )


@router.get("/player/{player_id}", response_model=PlayerIntelligenceOut)
async def get_player_intelligence(
    player_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Latest stats snapshot for a player."""
    from app.db.models.player import Player, PlayerStatsSnapshot

    result = await db.execute(select(Player).where(Player.id == player_id))
    player = result.scalar_one_or_none()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    # Latest stats snapshot
    snap_result = await db.execute(
        select(PlayerStatsSnapshot)
        .where(PlayerStatsSnapshot.player_id == player_id)
        .order_by(PlayerStatsSnapshot.snapshot_date.desc())
        .limit(1)
    )
    snap = snap_result.scalar_one_or_none()

    return PlayerIntelligenceOut(
        player_id=str(player_id),
        name=player.name,
        sport=player.sport,
        team=player.team,
        position=player.position,
        latest_stats=snap.stats if snap else None,
        stats_source=snap.source if snap else None,
        stats_date=snap.snapshot_date if snap else None,
    )


@router.get("/news", response_model=list[NewsOut])
async def get_news(
    match_id: Optional[uuid.UUID] = Query(None),
    hours: int = Query(24, le=168),
    limit: int = Query(20, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Latest news items, optionally filtered by match."""
    from app.db.models.news import NewsItem

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    filters = [NewsItem.fetched_at >= since]
    if match_id:
        filters.append(NewsItem.match_id == match_id)

    result = await db.execute(
        select(NewsItem)
        .where(and_(*filters))
        .order_by(NewsItem.fetched_at.desc())
        .limit(limit)
    )
    items = result.scalars().all()

    return [
        NewsOut(
            id=str(item.id),
            title=item.title,
            source=item.source,
            sentiment=float(item.sentiment) if item.sentiment is not None else None,
            relevance=float(item.relevance) if item.relevance is not None else None,
            published_at=item.published_at,
            team=item.team,
            match_id=str(item.match_id) if item.match_id else None,
        )
        for item in items
    ]


@router.get("/market/{match_id}", response_model=MarketIntelligenceOut)
async def get_market_intelligence(
    match_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Odds history and market signals for a match."""
    from app.db.models.match import Match, MatchOdds
    from app.db.models.context import MatchContext

    result = await db.execute(select(Match).where(Match.id == match_id))
    match = result.scalar_one_or_none()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    # All odds records (last 200)
    odds_result = await db.execute(
        select(MatchOdds)
        .where(MatchOdds.match_id == match_id)
        .order_by(MatchOdds.fetched_at.desc())
        .limit(200)
    )
    all_odds = odds_result.scalars().all()

    # Deduplicate for best available
    seen: set = set()
    best_available = []
    for o in all_odds:
        key = (o.bookmaker, o.market, o.outcome)
        if key not in seen:
            seen.add(key)
            best_available.append({
                "bookmaker": o.bookmaker,
                "market": o.market,
                "outcome": o.outcome,
                "odds": float(o.odds),
                "fetched_at": o.fetched_at.isoformat() if o.fetched_at else None,
            })

    # Context for market signals
    ctx_result = await db.execute(
        select(MatchContext).where(MatchContext.match_id == match_id)
    )
    ctx = ctx_result.scalar_one_or_none()

    # Odds history (all records, for charts)
    odds_history = [
        {
            "bookmaker": o.bookmaker,
            "market": o.market,
            "outcome": o.outcome,
            "odds": float(o.odds),
            "fetched_at": o.fetched_at.isoformat() if o.fetched_at else None,
        }
        for o in all_odds
    ]

    return MarketIntelligenceOut(
        match_id=str(match_id),
        display_name=match.display_name(),
        odds_history=odds_history,
        market_signals=ctx.market_signals if ctx else None,
        best_available=best_available[:30],
    )
