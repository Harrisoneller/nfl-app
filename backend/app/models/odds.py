"""Sportsbook odds lines (futures, awards, games)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._mixins import TimestampMixin


class OddsLine(Base, TimestampMixin):
    __tablename__ = "odds_lines"
    __table_args__ = (
        Index("ix_odds_market", "market"),
        Index("ix_odds_event", "event_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(64), nullable=False)  # 'h2h','spreads','totals','futures:mvp'
    event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    home_team: Mapped[str | None] = mapped_column(String(128), nullable=True)
    away_team: Mapped[str | None] = mapped_column(String(128), nullable=True)
    commence_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bookmaker: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)  # outcome label
    price: Mapped[int | None] = mapped_column(Integer, nullable=True)  # american odds
    point: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
