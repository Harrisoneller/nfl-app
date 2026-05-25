"""Feature-store snapshot rows for train/serve consistency checks."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._mixins import TimestampMixin


class FeatureSnapshot(Base, TimestampMixin):
    __tablename__ = "feature_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "season",
            "week",
            "game_id",
            "entity_id",
            "feature_set_version",
            "model_version",
            name="uq_feature_snapshot_identity",
        ),
        Index("ix_feature_snapshots_lookup", "season", "week", "game_id", "entity_id"),
        Index("ix_feature_snapshots_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    game_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    feature_set_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="inference")
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
