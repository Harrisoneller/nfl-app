"""Typed player metrics for SQL filter/sort (leaderboards)."""
from __future__ import annotations

from sqlalchemy import Boolean, Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._mixins import TimestampMixin


class PlayerMetricValue(Base, TimestampMixin):
    __tablename__ = "player_metric_values"
    __table_args__ = (
        UniqueConstraint("player_id", "season", "metric", name="uq_player_metric"),
        Index("ix_player_metric_season_pos_metric_pct", "season", "position", "metric", "percentile"),
        Index("ix_player_metric_season_pos_metric_val", "season", "position", "metric", "value"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    season: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    position: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    team_id: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    percentile: Mapped[float | None] = mapped_column(Float, nullable=True)
    higher_is_better: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
