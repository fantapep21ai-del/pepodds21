from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import String, DateTime, Numeric, ForeignKey, Boolean, Text, Integer
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base


class BettingOpportunity(Base):
    """
    A value bet identified by the consensus engine.
    EV = (model_probability × real_odds) - 1
    Only opportunities with EV > threshold reach the risk engine.
    """
    __tablename__ = "betting_opportunities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    match_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("matches.id"), nullable=False)

    market: Mapped[str] = mapped_column(String(100), nullable=False)
    outcome: Mapped[str] = mapped_column(String(200), nullable=False)
    bookmaker: Mapped[str] = mapped_column(String(100), nullable=False)
    best_odds: Mapped[float] = mapped_column(Numeric(8, 3), nullable=False)

    model_probability: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    consensus_votes: Mapped[Optional[dict]] = mapped_column(JSONB)
    uncertainty_score: Mapped[float] = mapped_column(Numeric(6, 4), default=0.0)

    expected_value: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False)

    # Tier system (S/A/B/C) + edge score
    tier: Mapped[str] = mapped_column(String(5), default="C")              # S | A | B | C
    edge: Mapped[Optional[float]] = mapped_column(Numeric(8, 6))          # EV * confidence
    bet_type: Mapped[str] = mapped_column(String(30), default="singola")  # singola | scalata | doppia | multipla
    confidence_level: Mapped[str] = mapped_column(String(20), default="normale")  # alta | normale | bassa

    # Scalata linkage
    scalata_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("scalate.id"), nullable=True)
    scalata_step: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Composite bet linkage (double/multiple)
    composite_bet_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("composite_bets.id"), nullable=True
    )

    reference_source: Mapped[Optional[str]] = mapped_column(String(30), default="agent_consensus")  # pinnacle_no_vig | agent_consensus
    status: Mapped[str] = mapped_column(String(30), default="pending")
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text)
    uncertainty_blocked: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    match: Mapped[Match] = relationship(back_populates="opportunities")  # noqa: F821
    bet: Mapped[Optional[Bet]] = relationship(back_populates="opportunity", uselist=False)  # noqa: F821
    composite_bet: Mapped[Optional[CompositeBet]] = relationship(  # noqa: F821
        "CompositeBet", back_populates="opportunity_legs",
        foreign_keys=[composite_bet_id]
    )
