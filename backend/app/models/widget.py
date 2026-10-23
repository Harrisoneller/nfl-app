"""Saved widgets — AI-generated or user-pinned."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._mixins import TimestampMixin

# JSONB on Postgres, plain JSON on SQLite so the public-read tests can
# persist a spec without a Postgres server. Postgres still sees JSONB.
_JSON = JSONB().with_variant(JSON(), "sqlite")


class Widget(Base, TimestampMixin):
    __tablename__ = "widgets"

    # uuid4, not a sequence. GET /widgets/{id}/public is unauthenticated and
    # treats this value as an unguessable capability URL. Do not switch it
    # to a serial integer without adding a separate random share token.
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    spec: Mapped[dict] = mapped_column(_JSON, nullable=False)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Existing rows stay shareable. The owner turns this off via POST /revoke.
    is_public: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_rendered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.now()
    )
