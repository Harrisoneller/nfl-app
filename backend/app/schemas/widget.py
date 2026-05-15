"""Widget specification + DB-output schemas.

A `WidgetSpec` is fully self-describing: it tells the frontend what kind
of view to render, where to fetch data from, and how to display it.
The AI emits these; the frontend renders them via a generic `<WidgetRenderer/>`.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

WidgetKind = Literal[
    "stat_card",
    "comparison_table",
    "list",
    "scoreboard",
    "bar_chart",
    "line_chart",
    "table",
    "news_list",
    "odds_table",
]


class WidgetDataSource(BaseModel):
    service: str  # e.g. "comparison" | "scores" | "stats" | "news" | "odds"
    method: str   # e.g. "compare_teams"
    params: dict[str, Any] = Field(default_factory=dict)


class WidgetDisplay(BaseModel):
    columns: list[str] = Field(default_factory=list)
    sort_by: str | None = None
    sort_dir: Literal["asc", "desc"] = "desc"
    limit: int | None = None
    highlight_winner: bool = False
    color_by: str | None = None  # e.g. "team_id"


class WidgetSpec(BaseModel):
    title: str
    kind: WidgetKind
    data_source: WidgetDataSource
    display: WidgetDisplay = Field(default_factory=WidgetDisplay)
    description: str = ""


class WidgetCreate(BaseModel):
    spec: WidgetSpec
    pinned: bool = False


class WidgetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    kind: str
    spec: dict[str, Any]
    pinned: bool
    sort_order: int
    last_rendered_at: datetime | None = None
    created_at: datetime
