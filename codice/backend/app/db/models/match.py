from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import String, DateTime, Boolean, Numeric, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base


class Competition(Base):
    __tablename__ = "competitions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sport: Mapped[str] = mapped_column(String(50), nullable=False)  # football | tennis | basketball
    tier: Mapped[str] = mapped_column(String(20), nullable=False)   # elite | standard
    weight: Mapped[float] = mapped_column(Numeric(4, 2), nullable=False, default=1.0)
    external_id: Mapped[Optional[str]] = mapped_column(String(100))
    odds_api_key: Mapped[Optional[str]] = mapped_column(String(100))

    matches: Mapped[list[Match]] = relationship(back_populates="competition")


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    competition_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("competitions.id"))
    home_team: Mapped[Optional[str]] = mapped_column(String(200))
    away_team: Mapped[Optional[str]] = mapped_column(String(200))
    player_a: Mapped[Optional[str]] = mapped_column(String(200))
    player_b: Mapped[Optional[str]] = mapped_column(String(200))
    match_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sport: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="scheduled")
    external_id: Mapped[Optional[str]] = mapped_column(String(200), unique=True)
    raw_stats: Mapped[Optional[dict]] = mapped_column(JSONB)
    analysis_status: Mapped[str] = mapped_column(String(30), default="pending")  # pending | complete | incomplete | no_data
    analysis_reason: Mapped[Optional[dict]] = mapped_column(JSONB)  # {"type": "incomplete", "reasons": [...]}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    competition: Mapped[Competition] = relationship(back_populates="matches")
    odds: Mapped[list[MatchOdds]] = relationship(back_populates="match")
    agent_runs: Mapped[list[AgentRun]] = relationship(back_populates="match")
    opportunities: Mapped[list[BettingOpportunity]] = relationship(back_populates="match")

    def display_name(self) -> str:
        if self.home_team:
            return f"{self.home_team} vs {self.away_team}"
        return f"{self.player_a} vs {self.player_b}"


class MatchOdds(Base):
    __tablename__ = "match_odds"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    match_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("matches.id"), nullable=False)
    bookmaker: Mapped[str] = mapped_column(String(100), nullable=False)
    market: Mapped[str] = mapped_column(String(100), nullable=False)
    outcome: Mapped[str] = mapped_column(String(200), nullable=False)
    odds: Mapped[float] = mapped_column(Numeric(8, 3), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_live: Mapped[bool] = mapped_column(Boolean, default=False)

    match: Mapped[Match] = relationship(back_populates="odds")
