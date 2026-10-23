"""Widget specification + DB-output schemas.

A `WidgetSpec` is fully self-describing: it tells the frontend what kind
of view to render, where to fetch data from, and how to display it.
The AI emits these; the frontend renders them via a generic `<WidgetRenderer/>`.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Public renders honor these caps. A stored spec can still be larger; the
# render path clamps to the same bounds so an anonymous fetch cannot ask
# for an unbounded query.
MAX_WIDGET_LIMIT = 100
MAX_WIDGET_LIST_ITEMS = 32

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


def _reject_unbounded_limit(value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"limit must be an integer from 1 to {MAX_WIDGET_LIMIT}")
    if value < 1 or value > MAX_WIDGET_LIMIT:
        raise ValueError(f"limit must be an integer from 1 to {MAX_WIDGET_LIMIT}")


class WidgetDataSource(BaseModel):
    service: str  # e.g. "comparison" | "scores" | "stats" | "news" | "odds"
    method: str   # e.g. "compare_teams"
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("params")
    @classmethod
    def params_are_bounded(cls, params: dict[str, Any]) -> dict[str, Any]:
        if params.get("limit") is not None:
            _reject_unbounded_limit(params["limit"])
        for key, value in params.items():
            if isinstance(value, list) and len(value) > MAX_WIDGET_LIST_ITEMS:
                raise ValueError(
                    f"{key} must contain at most {MAX_WIDGET_LIST_ITEMS} items"
                )
        return params


class WidgetDisplay(BaseModel):
    columns: list[str] = Field(default_factory=list, max_length=MAX_WIDGET_LIST_ITEMS)
    sort_by: str | None = None
    sort_dir: Literal["asc", "desc"] = "desc"
    limit: int | None = Field(default=None, ge=1, le=MAX_WIDGET_LIMIT)
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
    is_public: bool = True
    sort_order: int
    last_rendered_at: datetime | None = None
    created_at: datetime


class PublicWidget(BaseModel):
    """Widget definition safe to render without a login.

    Deliberately omits owner identity and dashboard bookkeeping
    (``user_id``, ``pinned``, ``sort_order``, timestamps).
    """

    id: uuid.UUID
    title: str
    kind: str
    spec: dict[str, Any]


class PublicWidgetResponse(BaseModel):
    widget: PublicWidget
    data: dict[str, Any]
