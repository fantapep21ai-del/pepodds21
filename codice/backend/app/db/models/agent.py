from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import String, DateTime, Numeric, ForeignKey, Text, Integer
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base


class AgentRun(Base):
    """One execution of one agent for one match."""
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    match_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("matches.id"), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    input_data: Mapped[Optional[dict]] = mapped_column(JSONB)
    output_data: Mapped[Optional[dict]] = mapped_column(JSONB)
    reasoning: Mapped[Optional[str]] = mapped_column(Text)
    tokens_used: Mapped[Optional[int]] = mapped_column(Integer)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    match: Mapped[Match] = relationship(back_populates="agent_runs")  # noqa: F821
    votes: Mapped[list[AgentVote]] = relationship(back_populates="agent_run")


class AgentVote(Base):
    """Probabilistic vote cast by one agent for a specific outcome."""
    __tablename__ = "agent_votes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_runs.id"), nullable=False)
    match_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("matches.id"), nullable=False)
    market: Mapped[str] = mapped_column(String(100), nullable=False)
    outcome: Mapped[str] = mapped_column(String(200), nullable=False)
    probability: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(6, 4), default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    agent_run: Mapped[AgentRun] = relationship(back_populates="votes")


class AgentScore(Base):
    """Rolling Brier score per agent — updated after each match settles."""
    __tablename__ = "agent_scores"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    brier_score: Mapped[float] = mapped_column(Numeric(8, 6), default=0.25)
    total_predictions: Mapped[int] = mapped_column(Integer, default=0)
    correct_predictions: Mapped[int] = mapped_column(Integer, default=0)
    weight: Mapped[float] = mapped_column(Numeric(6, 4), default=1.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
