"""Game + per-team game stats."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._mixins import TimestampMixin


class Game(Base, TimestampMixin):
    __tablename__ = "games"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # ESPN event id
    season: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    week: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    season_type: Mapped[int] = mapped_column(Integer, nullable=False, default=2)  # 1=pre,2=reg,3=post
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="scheduled")
    status_detail: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    home_team_id: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("teams.id"), nullable=True, index=True
    )
    away_team_id: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("teams.id"), nullable=True, index=True
    )
    home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    venue: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    broadcast: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class GameStat(Base, TimestampMixin):
    """Per-game per-team aggregated stats (yards, turnovers, etc.)."""

    __tablename__ = "game_stats"
    __table_args__ = (UniqueConstraint("game_id", "team_id", name="uq_game_team"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("games.id", ondelete="CASCADE"), nullable=False, index=True
    )
    team_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("teams.id"), nullable=False, index=True
    )
    stats: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
