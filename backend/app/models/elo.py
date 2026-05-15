"""Weekly team Elo ratings.

One row per (team_id, season, week). The "current" rating is the
highest (season, week) per team. We retain history so the team page
can show a season-long Elo trend.
"""
from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._mixins import TimestampMixin


class TeamEloRating(Base, TimestampMixin):
    __tablename__ = "team_elo_ratings"
    __table_args__ = (
        UniqueConstraint("team_id", "season", "week", name="uq_team_elo_week"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("teams.id"), index=True, nullable=False
    )
    season: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # Week 0 = pre-season carryover rating; Week N = post-game-N rating
    week: Mapped[int] = mapped_column(Integer, nullable=False)
    rating: Mapped[float] = mapped_column(Float, nullable=False, default=1500.0)
