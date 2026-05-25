from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class ExperimentEvent(Base):
    __tablename__ = "experiment_events"
    __table_args__ = (
        Index("ix_experiment_events_experiment_created", "experiment_key", "created_at"),
        Index("ix_experiment_events_variant_created", "variant", "created_at"),
        Index("ix_experiment_events_session_created", "session_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experiment_key: Mapped[str] = mapped_column(String(120), nullable=False)
    variant: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    page: Mapped[str] = mapped_column(String(80), nullable=False, default="unknown")
    card_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
