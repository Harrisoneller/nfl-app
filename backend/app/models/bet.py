"""User bet tracking — the bet-tracker + CLV profile.

A ``Bet`` is one wager a user logged (manually or one-tap from the odds/Sparky
pages). Straight bets have exactly one ``BetLeg``; parlays have two or more.

Settlement is idempotent and automatic: a background/endpoint pass grades every
pending leg whose game has gone final (mirroring Sparky's settle pattern) and
computes **closing line value (CLV)** for each leg against the append-only
``odds_snapshots`` closing capture (T4). CLV is price-based for moneylines and
line-based (points) for spreads/totals — see ``services/bets/grading.py``.

Money is tracked primarily in **units** (``stake_units``); a dollar stake is
optional (``stake_dollars``) for users who want real P&L. Results are stored as
profit/loss (``result_units`` / ``result_dollars``), so ROI = profit / staked.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ._mixins import TimestampMixin

# Status vocab shared by bets and legs.
PENDING = "pending"
WON = "won"
LOST = "lost"
PUSH = "push"
VOID = "void"


class Bet(Base, TimestampMixin):
    """One logged wager (straight = 1 leg, parlay = 2+ legs)."""

    __tablename__ = "bets"
    __table_args__ = (
        Index("ix_bets_user_placed", "user_id", "placed_at"),
        Index("ix_bets_user_status", "user_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    bet_type: Mapped[str] = mapped_column(String(16), nullable=False, default="straight")  # straight | parlay
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=PENDING)
    # Where the bet was entered from — powers "manual vs one-tap" analytics.
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")  # manual | odds | sparky
    note: Mapped[str] = mapped_column(String(280), nullable=False, default="")

    # Stake in units (primary) + optional real dollars.
    stake_units: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    stake_dollars: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Combined price for the whole bet (single leg's price for straights, the
    # product for parlays). Stored in both notations for convenience.
    odds_american: Mapped[int] = mapped_column(Integer, nullable=False)
    odds_decimal: Mapped[float] = mapped_column(Float, nullable=False)

    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Settlement outcome (profit/loss, not including stake return on the loss side).
    payout_units: Mapped[float | None] = mapped_column(Float, nullable=True)   # total returned incl. stake on a win
    result_units: Mapped[float | None] = mapped_column(Float, nullable=True)   # profit (+) / loss (-)
    result_dollars: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Roll-up CLV across legs: avg price-CLV% over moneyline legs, and whether the
    # bet (majority of legs) beat the closing number.
    clv_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    beat_close: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    legs: Mapped[list["BetLeg"]] = relationship(
        "BetLeg",
        back_populates="bet",
        cascade="all, delete-orphan",
        order_by="BetLeg.id",
        lazy="selectin",
    )


class BetLeg(Base, TimestampMixin):
    """A single selection within a bet."""

    __tablename__ = "bet_legs"
    __table_args__ = (
        Index("ix_bet_legs_bet", "bet_id"),
        Index("ix_bet_legs_event", "event_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bets.id", ondelete="CASCADE"), nullable=False
    )

    # Match keys for grading + CLV. event_id ties to odds_snapshots / The Odds API.
    event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    game_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    market: Mapped[str] = mapped_column(String(16), nullable=False)  # spread | total | moneyline | player_prop
    # team_id for spread/moneyline; "over"/"under" for totals and player props.
    selection: Mapped[str] = mapped_column(String(16), nullable=False)
    selection_label: Mapped[str] = mapped_column(String(128), nullable=False, default="")  # e.g. "PHI -3.5"
    line: Mapped[float | None] = mapped_column(Float, nullable=True)  # handicap / total; None for moneyline

    # Player-prop legs only: who + which prop market ("player_rush_yds", ...).
    # Grading matches the weekly stats frame; CLV matches player_prop_snapshots.
    player_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prop_market: Mapped[str | None] = mapped_column(String(48), nullable=True)

    odds_american: Mapped[int] = mapped_column(Integer, nullable=False)
    odds_decimal: Mapped[float] = mapped_column(Float, nullable=False)

    home_team_id: Mapped[str | None] = mapped_column(String(8), nullable=True)
    away_team_id: Mapped[str | None] = mapped_column(String(8), nullable=True)
    commence_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Filled at settle time.
    closing_line: Mapped[float | None] = mapped_column(Float, nullable=True)
    closing_odds_american: Mapped[int | None] = mapped_column(Integer, nullable=True)
    clv_pct: Mapped[float | None] = mapped_column(Float, nullable=True)   # price-based (moneyline)
    clv_line: Mapped[float | None] = mapped_column(Float, nullable=True)  # points (spread/total), +=better
    beat_close: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    leg_result: Mapped[str] = mapped_column(String(16), nullable=False, default=PENDING)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    bet: Mapped["Bet"] = relationship("Bet", back_populates="legs")
