"""NFL player.

Sourced from Sleeper + nfl-data-py rosters.
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._mixins import TimestampMixin


class Player(Base, TimestampMixin):
    __tablename__ = "players"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # sleeper player_id
    gsis_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    espn_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    full_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    position: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    team_id: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True
    )
    jersey_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[str | None] = mapped_column(String(8), nullable=True)
    weight: Mapped[int | None] = mapped_column(Integer, nullable=True)
    college: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="")  # Active, IR, etc.
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
