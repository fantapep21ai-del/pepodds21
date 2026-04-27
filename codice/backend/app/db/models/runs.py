from __future__ import annotations
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, DateTime, Integer, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class PipelineRun(Base):
    """Log of each full pipeline execution."""
    __tablename__ = "pipeline_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), default="running")
    matches_processed: Mapped[int] = mapped_column(Integer, default=0)
    opportunities_found: Mapped[int] = mapped_column(Integer, default=0)
    bets_placed: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text)
    meta: Mapped[Optional[dict]] = mapped_column(JSONB)
