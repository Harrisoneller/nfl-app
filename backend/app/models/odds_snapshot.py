"""Append-only odds snapshots — the line-movement history Sparky runs on.

The existing `odds_lines` table is a *current* snapshot: `odds_service.refresh_odds`
deletes and rewrites it on every pull, so it can answer "what's the line right
now?" but not "how did the line move?". Sparky's marquee signals (steam, reverse
line movement, late movement, market compression, false stability) are all about
*movement over time*, so they need history.

`odds_snapshots` is therefore strictly append-only. One row = one bookmaker's
moneyline (and, when present, spread/total) for one game at one capture time.
Each pull adds a fresh batch of rows; nothing is ever overwritten. A
`snapshot_label` (T1/T2/T3/T4) is assigned at capture time by how close the pull
is to kickoff, matching the spec's T1/T2/T3 movement-delta vocabulary.

Storage is cheap (a handful of NFL games × ~8 books × a few pulls per game), so
we keep raw per-book rows rather than pre-aggregating; consensus is computed on
read by taking the median across books.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._mixins import TimestampMixin


class OddsSnapshot(Base, TimestampMixin):
    __tablename__ = "odds_snapshots"
    __table_args__ = (
        # Movement queries fan out from a single event ordered by capture time.
        Index("ix_odds_snap_event_captured", "event_id", "captured_at"),
        Index("ix_odds_snap_event_book", "event_id", "book"),
        Index("ix_odds_snap_label", "snapshot_label"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # The Odds API event id (stable across snapshots for the same game).
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # T1/T2/T3/T4 bucket relative to kickoff (see sparky.snapshots for the rule).
    snapshot_label: Mapped[str | None] = mapped_column(String(8), nullable=True)
    commence_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Full team names (as The Odds API returns them) + our canonical 3-letter ids.
    home_team: Mapped[str | None] = mapped_column(String(128), nullable=True)
    away_team: Mapped[str | None] = mapped_column(String(128), nullable=True)
    home_team_id: Mapped[str | None] = mapped_column(String(8), nullable=True)
    away_team_id: Mapped[str | None] = mapped_column(String(8), nullable=True)

    book: Mapped[str] = mapped_column(String(64), nullable=False)

    # Moneyline (american). Spread/total are stored when the pull includes them.
    home_ml: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_ml: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_spread: Mapped[float | None] = mapped_column(Float, nullable=True)
    away_spread: Mapped[float | None] = mapped_column(Float, nullable=True)
    total: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Two-way de-vigged win probabilities derived from the moneyline pair.
    home_implied: Mapped[float | None] = mapped_column(Float, nullable=True)
    away_implied: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 'home' | 'away' | None — which side this book has as the favorite.
    favorite: Mapped[str | None] = mapped_column(String(8), nullable=True)

    raw: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
