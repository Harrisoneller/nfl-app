"""Widget specification + DB-output schemas.

A `WidgetSpec` is fully self-describing: it tells the frontend what kind
of view to render, where to fetch data from, and how to display it.
The AI emits these; the frontend renders them via a generic `<WidgetRenderer/>`.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..utils.seasons import SEASON_RANGE_START, current_or_upcoming_season

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


# NFL team primary keys are short abbreviations (PHI, KC, LV). The column is
# String(8). Digits stay allowed so a stored spec is not rejected for shape
# alone; symbols and other types are not team ids.
_TEAM_ID = re.compile(r"^[A-Z0-9]{2,8}$")


class WidgetQueryError(ValueError):
    """A widget spec param has the wrong type or an impossible value."""


def _reject_unbounded_limit(value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"limit must be an integer from 1 to {MAX_WIDGET_LIMIT}")
    if value < 1 or value > MAX_WIDGET_LIMIT:
        raise ValueError(f"limit must be an integer from 1 to {MAX_WIDGET_LIMIT}")


def _season_error(value: Any) -> str | None:
    """Season is a calendar year, not a bool or a stringified number."""
    if isinstance(value, bool) or not isinstance(value, int):
        return "season must be an integer"
    latest = current_or_upcoming_season()
    if value < SEASON_RANGE_START or value > latest:
        return f"season must be an integer from {SEASON_RANGE_START} to {latest}"
    return None


def _clean_team_id(value: Any, key: str) -> str:
    if not isinstance(value, str):
        raise WidgetQueryError(f"{key} must be a 2 to 8 character team abbreviation")
    cleaned = value.strip().upper()
    if _TEAM_ID.fullmatch(cleaned) is None:
        raise WidgetQueryError(f"{key} must be a 2 to 8 character team abbreviation")
    return cleaned


def checked_params(params: Any) -> dict[str, Any]:
    """Return params with season and team ids checked.

    Missing season and team id stay missing. Callers that need a team id
    still have to require it. A present value of the wrong type, or a season
    outside the offered range, is rejected.
    """
    if params is None:
        return {}
    if not isinstance(params, dict):
        raise WidgetQueryError("params must be an object")
    cleaned = dict(params)
    if "season" in cleaned:
        error = _season_error(cleaned["season"])
        if error:
            raise WidgetQueryError(error)
    if "team_id" in cleaned:
        cleaned["team_id"] = _clean_team_id(cleaned["team_id"], "team_id")
    if "team_ids" in cleaned:
        team_ids = cleaned["team_ids"]
        if not isinstance(team_ids, list):
            raise WidgetQueryError("team_ids must be a list of team abbreviations")
        cleaned["team_ids"] = [_clean_team_id(item, "team_ids") for item in team_ids]
    return cleaned


class WidgetDataSource(BaseModel):
    service: str  # e.g. "comparison" | "scores" | "stats" | "news" | "odds"
    method: str   # e.g. "compare_teams"
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("params")
    @classmethod
    def params_are_bounded(cls, params: dict[str, Any]) -> dict[str, Any]:
        params = checked_params(params)
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
