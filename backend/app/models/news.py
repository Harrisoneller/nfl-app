"""Aggregated news + social posts."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._mixins import TimestampMixin


class NewsItem(Base, TimestampMixin):
    __tablename__ = "news_items"
    __table_args__ = (
        Index("ix_news_published_at", "published_at"),
        Index("ix_news_source", "source"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # hash of (source, link)
    source: Mapped[str] = mapped_column(String(64), nullable=False)  # 'espn-rss', 'reddit', 'twitter'
    source_label: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    link: Mapped[str] = mapped_column(String(1024), nullable=False)
    author: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    image_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    team_tags: Mapped[str] = mapped_column(String(255), nullable=False, default="")  # csv of team ids
