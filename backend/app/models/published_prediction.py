"""Canonical stored game prediction, one row per game id.

The slate, H2H, team schedule, edges, and Sparky all read this row. A refresh
writes a new payload in place. Readers keep seeing the previous row until that
write commits, so a refresh does not open an empty slate window.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._mixins import TimestampMixin

_JSON = JSONB().with_variant(JSON(), "sqlite")


class PublishedGamePrediction(Base, TimestampMixin):
    __tablename__ = "published_game_predictions"
    __table_args__ = (
        Index("ix_published_pred_season_week", "season", "week"),
        Index("ix_published_pred_revision", "input_revision"),
    )

    game_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_team_id: Mapped[str | None] = mapped_column(String(8), nullable=True)
    away_team_id: Mapped[str | None] = mapped_column(String(8), nullable=True)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    # Headline step that produced the numbers (market-blend-v1 today).
    # A later pipeline change must not serve these rows as the new model.
    headline_pipeline: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    prediction_basis: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict] = mapped_column(_JSON, nullable=False)
    frozen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    kickoff_unknown: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    kickoff_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PredictionRefreshLease(Base, TimestampMixin):
    """Single-row lease so overlapping refreshes coalesce instead of stacking."""

    __tablename__ = "prediction_refresh_leases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resume_after: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Bumps on every successful publication commit. Cache keys read this so a
    # republish is visible even when the input revision did not change.
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
