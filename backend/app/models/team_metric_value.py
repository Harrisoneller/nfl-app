"""Typed team metrics for SQL filter/sort (leaderboards)."""
from __future__ import annotations

from sqlalchemy import Boolean, Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._mixins import TimestampMixin


class TeamMetricValue(Base, TimestampMixin):
    __tablename__ = "team_metric_values"
    __table_args__ = (
        UniqueConstraint("team_id", "season", "metric", name="uq_team_metric"),
        Index("ix_team_metric_season_metric_pct", "season", "metric", "percentile"),
        Index("ix_team_metric_season_metric_val", "season", "metric", "value"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    season: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    percentile: Mapped[float | None] = mapped_column(Float, nullable=True)
    higher_is_better: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
