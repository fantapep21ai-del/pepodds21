from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING
from sqlalchemy import String, DateTime, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.bet import Bet
    from app.db.models.opportunity import BettingOpportunity


class CompositeBet(Base):
    """
    A double (2 events) or multiple (4-6 events) bet built from individual opportunities.

    EV validation:
      double:   combined_prob * combined_odds - 1 ≥ 5%
      multiple: combined_prob * combined_odds - 1 ≥ 8%

    Stake:
      double:   0.6 * single_stake
      multiple: 0.2 * single_stake
    """
    __tablename__ = "composite_bets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bet_type: Mapped[str] = mapped_column(String(20), nullable=False)       # double | multiple
    status: Mapped[str] = mapped_column(String(30), default="open")         # open | won | lost | void
    combined_odds: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    combined_prob: Mapped[float] = mapped_column(Numeric(8, 6), nullable=False)
    expected_value: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False)
    stake: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    result: Mapped[Optional[str]] = mapped_column(String(30))
    pnl: Mapped[Optional[float]] = mapped_column(Numeric(10, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    settled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    notes: Mapped[Optional[str]] = mapped_column(Text)

    # legs are linked via Bet.composite_bet_id FK
    legs: Mapped[list[Bet]] = relationship(  # type: ignore[name-defined]
        "Bet", back_populates="composite_bet", foreign_keys="Bet.composite_bet_id"
    )
    opportunity_legs: Mapped[list[BettingOpportunity]] = relationship(  # type: ignore[name-defined]
        "BettingOpportunity", back_populates="composite_bet",
        foreign_keys="BettingOpportunity.composite_bet_id"
    )
