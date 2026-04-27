from __future__ import annotations
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, DateTime, Numeric, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class NewsItem(Base):
    """
    News article or feed item related to a match, team, or player.
    Sentiment and relevance are computed via NLP (Claude or keyword scoring).
    """
    __tablename__ = "news_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    match_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("matches.id"), nullable=True, index=True
    )
    player_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("players.id"), nullable=True
    )
    team: Mapped[Optional[str]] = mapped_column(String(200))

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[Optional[str]] = mapped_column(Text)
    source: Mapped[Optional[str]] = mapped_column(String(200))
    url: Mapped[Optional[str]] = mapped_column(String(1000))

    # -1.0 (very negative) → +1.0 (very positive)
    sentiment: Mapped[Optional[float]] = mapped_column(Numeric(4, 3))
    # 0.0 (irrelevant) → 1.0 (highly relevant to bet decision)
    relevance: Mapped[Optional[float]] = mapped_column(Numeric(4, 3))

    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, index=True
    )
