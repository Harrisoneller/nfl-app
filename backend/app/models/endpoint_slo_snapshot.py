from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class EndpointSloSnapshot(Base):
    __tablename__ = "endpoint_slo_snapshots"
    __table_args__ = (
        Index("ix_endpoint_slo_snapshots_endpoint_window", "endpoint_key", "window_started_at"),
        Index("ix_endpoint_slo_snapshots_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    endpoint_key: Mapped[str] = mapped_column(String(160), nullable=False)
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    status_bucket: Mapped[str] = mapped_column(String(16), nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    p50_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    p95_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    p99_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cache_hit_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
