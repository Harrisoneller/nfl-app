"""nflverse seasonal player stats (enriched), materialized for fast reads."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._mixins import TimestampMixin


class PlayerSeasonStat(Base, TimestampMixin):
    __tablename__ = "player_season_stats"
    __table_args__ = (UniqueConstraint("player_id", "season", name="uq_player_season"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    season: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    position: Mapped[str | None] = mapped_column(String(8), nullable=True)
    team_id: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("teams.id"), nullable=True, index=True,
    )
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    stats: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
