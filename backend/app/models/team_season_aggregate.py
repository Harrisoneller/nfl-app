"""Team PBP-derived aggregates per season (materialized from nflverse)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._mixins import TimestampMixin


class TeamSeasonAggregate(Base, TimestampMixin):
    __tablename__ = "team_season_aggregates"
    __table_args__ = (UniqueConstraint("team_id", "season", name="uq_team_season_agg"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("teams.id"), nullable=False, index=True,
    )
    season: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
