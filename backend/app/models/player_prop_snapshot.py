"""Append-only player-prop odds snapshots.

Same design contract as ``odds_snapshots`` (see that module's docstring): one
row = one bookmaker's line for one player-prop market at one capture time.
Nothing is ever overwritten, so line movement history is preserved for CLV and
future prop-movement signals. Consensus is computed on read (median across
books at the latest capture).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._mixins import TimestampMixin


class PlayerPropSnapshot(Base, TimestampMixin):
    __tablename__ = "player_prop_snapshots"
    __table_args__ = (
        Index("ix_prop_snap_event_captured", "event_id", "captured_at"),
        Index("ix_prop_snap_player_market", "player_id", "market"),
        Index("ix_prop_snap_name_market", "player_name", "market"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # The Odds API event id (joins to odds_snapshots.event_id for the same game).
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    commence_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    home_team_id: Mapped[str | None] = mapped_column(String(8), nullable=True)
    away_team_id: Mapped[str | None] = mapped_column(String(8), nullable=True)

    book: Mapped[str] = mapped_column(String(64), nullable=False)
    # The Odds API market key, e.g. "player_rush_yds", "player_anytime_td".
    market: Mapped[str] = mapped_column(String(48), nullable=False)

    # Player name as the book lists it + our player id when matched.
    player_name: Mapped[str] = mapped_column(String(128), nullable=False)
    player_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Over/Under line (NULL for yes/no markets like anytime TD).
    line: Mapped[float | None] = mapped_column(Float, nullable=True)
    over_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    under_price: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Two-way de-vigged probabilities (over/yes side; None if one-sided).
    over_implied: Mapped[float | None] = mapped_column(Float, nullable=True)
    under_implied: Mapped[float | None] = mapped_column(Float, nullable=True)

    raw: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
