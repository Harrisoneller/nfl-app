"""NFL team."""
from __future__ import annotations

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._mixins import TimestampMixin


class Team(Base, TimestampMixin):
    __tablename__ = "teams"

    # 'PHI', 'SF', etc. ESPN uses these as the primary identifier across endpoints.
    id: Mapped[str] = mapped_column(String(8), primary_key=True)
    espn_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    market: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    full_name: Mapped[str] = mapped_column(String(128), nullable=False)
    conference: Mapped[str] = mapped_column(String(8), nullable=False, default="")  # AFC | NFC
    division: Mapped[str] = mapped_column(String(16), nullable=False, default="")  # East/West/...
    primary_color: Mapped[str] = mapped_column(String(8), nullable=False, default="#111827")
    secondary_color: Mapped[str] = mapped_column(String(8), nullable=False, default="#9ca3af")
    logo_url: Mapped[str] = mapped_column(String(512), nullable=False, default="")
