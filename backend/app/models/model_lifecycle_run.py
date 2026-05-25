"""Lifecycle runs for weekly train/backtest/calibrate/promote decisions."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._mixins import TimestampMixin


class ModelLifecycleRun(Base, TimestampMixin):
    __tablename__ = "model_lifecycle_runs"
    __table_args__ = (
        Index("ix_model_lifecycle_runs_started_at", "started_at"),
        Index("ix_model_lifecycle_runs_model_version", "model_version"),
        Index("ix_model_lifecycle_runs_promoted", "is_promoted"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    season: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    decision: Mapped[str] = mapped_column(String(32), nullable=False, default="hold")
    is_promoted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    champion_model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    train_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    backtest_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    calibration_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    compare_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    gate_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
