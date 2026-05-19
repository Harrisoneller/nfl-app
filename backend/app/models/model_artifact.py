"""Persistent cache for expensive model outputs.

Generic JSONB blob keyed by (kind, key). The Python service layer treats
this as an L2 cache below the in-process TTL cache:

    request → in-process cache (μs) → DB artifact (ms) → recompute (s) → both stores

Completed-season data lives effectively forever once written; current-season
artifacts get a short TTL via `valid_until` so the proactive warmup job
knows when to refresh.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._mixins import TimestampMixin


class ModelArtifact(Base, TimestampMixin):
    __tablename__ = "model_artifacts"
    __table_args__ = (
        # The composite (kind, key) is the natural unique identity.
        Index("ix_model_artifact_kind_key", "kind", "key", unique=True),
        Index("ix_model_artifact_valid_until", "valid_until"),
    )

    # Synthetic surrogate key so we don't fight with PKs on upsert.
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # `kind` discriminates the artifact type (e.g. 'team_profile',
    # 'monte_carlo_sim', 'awards'). `key` is a deterministic identifier
    # within that kind ('team:PHI:season:2024').
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(255), nullable=False)

    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # When the artifact stops being "fresh." NULL = never expires (used for
    # completed-season data that's immutable).
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
