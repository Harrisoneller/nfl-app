"""Sparky model-output tables: predictions, parlay rankings, and settled results.

These persist the intelligence layer's outputs so the dashboard, parlay views,
and historical-accuracy reporting all read from the same source of truth rather
than recomputing on every request.

- ``SparkyGamePrediction``  — one row per game per slate (predicted winner,
  ensemble confidence, signal tags, plain-English explanation).
- ``SparkyParlayRanking``   — persisted ranked 3-leg parlay combinations
  (the auto-generated daily "recommended" parlays; the interactive builder
  computes on the fly and only persists when asked).
- ``SparkyHistoricalResult``— per-game settlement used for individual-pick
  accuracy, accuracy-by-signal, and accuracy-by-confidence-band.
- ``SparkyParlayResult``     — per-slate parlay settlement used for rank-#1 hit
  rate and Top-3 / Top-4 containment.

A ``slate`` is the set of games for a given day; ``slate_date`` is the UTC date
the games belong to. ``slate_id`` is a deterministic key for a specific 3-game
parlay set (sorted event ids joined by ``|``).
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._mixins import TimestampMixin


class SparkyGamePrediction(Base, TimestampMixin):
    """Individual game prediction + signals for one slate."""

    __tablename__ = "sparky_game_predictions"
    __table_args__ = (
        UniqueConstraint("slate_date", "event_id", name="uq_sparky_pred_slate_event"),
        Index("ix_sparky_pred_slate", "slate_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slate_date: Mapped[date] = mapped_column(Date, nullable=False)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    game_id: Mapped[str | None] = mapped_column(String(32), nullable=True)  # ESPN id when matched

    home_team_id: Mapped[str | None] = mapped_column(String(8), nullable=True)
    away_team_id: Mapped[str | None] = mapped_column(String(8), nullable=True)
    home_team: Mapped[str | None] = mapped_column(String(128), nullable=True)
    away_team: Mapped[str | None] = mapped_column(String(128), nullable=True)
    commence_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    predicted_winner: Mapped[str | None] = mapped_column(String(8), nullable=True)  # team id
    # Ensemble win prob for the predicted winner (0-1).
    win_prob: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    # Component probabilities (for transparency / debugging the ensemble).
    model_prob: Mapped[float | None] = mapped_column(Float, nullable=True)   # Elo/ML base
    market_prob: Mapped[float | None] = mapped_column(Float, nullable=True)  # de-vigged market
    # 0-100 confidence after signal boosts/penalties.
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=50.0)
    # anchor | strong_lean | lean | coin_flip | upset_watch
    classification: Mapped[str | None] = mapped_column(String(24), nullable=True)

    # list[{key,label,side,severity,magnitude,weight,explanation}]
    signals: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Snapshot of the consensus market line used (best ml each side, books, etc.)
    market: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class SparkyParlayRanking(Base, TimestampMixin):
    """A single ranked 3-leg parlay combination."""

    __tablename__ = "sparky_parlay_rankings"
    __table_args__ = (
        Index("ix_sparky_parlay_slate", "slate_id"),
        Index("ix_sparky_parlay_slate_date", "slate_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slate_id: Mapped[str] = mapped_column(String(200), nullable=False)  # "evtA|evtB|evtC" sorted
    slate_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)

    # Three legs: event id + the side picked (team id) for each.
    leg1_event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    leg2_event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    leg3_event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    leg1_pick: Mapped[str | None] = mapped_column(String(8), nullable=True)
    leg2_pick: Mapped[str | None] = mapped_column(String(8), nullable=True)
    leg3_pick: Mapped[str | None] = mapped_column(String(8), nullable=True)

    parlay_odds_american: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parlay_odds_decimal: Mapped[float | None] = mapped_column(Float, nullable=True)
    implied_prob: Mapped[float | None] = mapped_column(Float, nullable=True)      # from parlay odds
    combined_win_prob: Mapped[float | None] = mapped_column(Float, nullable=True)  # model product
    underdog_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    signal_alignment: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    composite_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    legs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)  # full per-leg detail


class SparkyHistoricalResult(Base, TimestampMixin):
    """Settled individual-pick outcome for accuracy reporting."""

    __tablename__ = "sparky_historical_results"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_sparky_result_event"),
        Index("ix_sparky_result_slate_date", "slate_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    game_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    slate_date: Mapped[date] = mapped_column(Date, nullable=False)
    sport: Mapped[str] = mapped_column(String(16), nullable=False, default="NFL")

    predicted_winner: Mapped[str | None] = mapped_column(String(8), nullable=True)
    actual_winner: Mapped[str | None] = mapped_column(String(8), nullable=True)
    prediction_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    classification: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # Signal keys present on this pick (for accuracy-by-signal breakdowns).
    signal_keys: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SparkyParlayResult(Base, TimestampMixin):
    """Settled parlay outcome for one slate — drives rank-#1 and containment rates."""

    __tablename__ = "sparky_parlay_results"
    __table_args__ = (
        UniqueConstraint("slate_id", name="uq_sparky_parlay_result_slate"),
        Index("ix_sparky_parlay_result_date", "slate_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slate_id: Mapped[str] = mapped_column(String(200), nullable=False)
    slate_date: Mapped[date] = mapped_column(Date, nullable=False)
    sport: Mapped[str] = mapped_column(String(16), nullable=False, default="NFL")

    n_parlays: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    # Rank (1-based) of the combination that actually hit, if any.
    winning_combo_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rank_1_hit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    top_3_containment: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    top_4_containment: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
