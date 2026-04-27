from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import String, DateTime, Numeric, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import Optional
from app.db.base import Base


class Bet(Base):
    """
    A bet actually placed (manually or auto) following an approved opportunity.
    P&L is settled when the match result is confirmed.
    """
    __tablename__ = "bets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("betting_opportunities.id"), nullable=False, unique=True
    )

    bookmaker: Mapped[str] = mapped_column(String(100), nullable=False)
    market: Mapped[str] = mapped_column(String(100), nullable=False)
    outcome: Mapped[str] = mapped_column(String(200), nullable=False)
    odds: Mapped[float] = mapped_column(Numeric(8, 3), nullable=False)
    stake: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    status: Mapped[str] = mapped_column(String(30), default="open")
    result: Mapped[Optional[str]] = mapped_column(String(30))
    pnl: Mapped[Optional[float]] = mapped_column(Numeric(10, 2))
    settled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    notes: Mapped[Optional[str]] = mapped_column(Text)

    # CLV (Closing Line Value) tracking
    closing_odds: Mapped[Optional[float]] = mapped_column(Numeric(8, 3))   # odds at market close
    clv: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))            # (closing/placed - 1) * 100

    # Composite bet linkage
    composite_bet_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("composite_bets.id"), nullable=True
    )

    # Scalata integration — link a scalata se bet è uno step di cascata
    scalata_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scalate.id"), nullable=True
    )

    # Actual odds confirmed at placement (può differire da best_odds se cambiano velocemente)
    actual_odds: Mapped[Optional[float]] = mapped_column(Numeric(8, 3))

    opportunity: Mapped[BettingOpportunity] = relationship(back_populates="bet")  # noqa: F821
    composite_bet: Mapped[Optional[CompositeBet]] = relationship(  # noqa: F821
        "CompositeBet", back_populates="legs", foreign_keys=[composite_bet_id]
    )
