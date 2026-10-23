"""Widget CRUD and rendering.

`render_widget` resolves a WidgetSpec by routing to the appropriate
service. It's used both directly from the /widgets/{id}/render endpoint
and from the AI's `build_widget` tool.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from ..models.widget import Widget
from ..schemas.widget import MAX_WIDGET_LIMIT, MAX_WIDGET_LIST_ITEMS, WidgetSpec
from . import comparison_service, news_service, odds_service, scores_service, stats_service

# Keys that must never appear on the anonymous share payload, at any depth.
# Widget specs are JSONB and rendered payloads are free-form, so a denylist
# backs the spec whitelist below.
_PRIVATE_KEYS = frozenset(
    {
        "user_id",
        "userid",
        "owner_id",
        "email",
        "e_mail",
        "owner_email",
        "chat",
        "chats",
        "chat_content",
        "chat_message",
        "chat_messages",
        "transcript",
        "private_notes",
        "private_note",
        "notes",
        "note",
        "hashed_password",
        "password",
        "password_hash",
    }
)

_DISPLAY_KEYS = (
    "columns",
    "sort_by",
    "sort_dir",
    "limit",
    "highlight_winner",
    "color_by",
)

# Params that are lists of ids or names. A non-list value must not be iterated.
_LIST_PARAM_KEYS = frozenset({"team_ids", "names", "columns", "ids"})


def bound_limit(value: Any) -> int | None:
    """Clamp a limit into 1..MAX_WIDGET_LIMIT. Unusable values become None."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 1:
        return None
    return min(value, MAX_WIDGET_LIMIT)


def bound_params(params: Any) -> dict[str, Any]:
    """Cap `limit` and every list param. Drop a limit that is not a positive int."""
    raw = params if isinstance(params, dict) else {}
    bounded: dict[str, Any] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        if key == "limit":
            limit = bound_limit(value)
            if limit is not None:
                bounded[key] = limit
            continue
        if key in _LIST_PARAM_KEYS:
            bounded[key] = list(value[:MAX_WIDGET_LIST_ITEMS]) if isinstance(value, list) else []
            continue
        if isinstance(value, list):
            bounded[key] = value[:MAX_WIDGET_LIST_ITEMS]
            continue
        bounded[key] = value
    return bounded


def bound_display(display: Any) -> dict[str, Any]:
    raw = display if isinstance(display, dict) else {}
    kept = {key: raw[key] for key in _DISPLAY_KEYS if key in raw}
    columns = kept.get("columns")
    if "columns" in kept:
        if isinstance(columns, list):
            kept["columns"] = [
                item for item in columns[:MAX_WIDGET_LIST_ITEMS] if isinstance(item, str)
            ]
        else:
            kept["columns"] = []
    if "limit" in kept:
        limit = bound_limit(kept["limit"])
        if limit is None:
            kept.pop("limit")
        else:
            kept["limit"] = limit
    return kept


def strip_private(value: Any) -> Any:
    """Drop owner, chat, and note fields recursively. Leave other values intact."""
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or key.lower() in _PRIVATE_KEYS:
                continue
            cleaned[key] = strip_private(item)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [strip_private(item) for item in value]
    return value


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def public_spec(spec: dict[str, Any] | None) -> dict[str, Any]:
    """Whitelist the WidgetSpec fields a shared page is allowed to render."""
    raw = spec if isinstance(spec, dict) else {}
    source = raw.get("data_source") if isinstance(raw.get("data_source"), dict) else {}
    display = raw.get("display") if isinstance(raw.get("display"), dict) else {}
    params = source.get("params") if isinstance(source.get("params"), dict) else {}
    return {
        "title": _text(raw.get("title")),
        "kind": _text(raw.get("kind")),
        "description": _text(raw.get("description")),
        "data_source": {
            "service": _text(source.get("service")),
            "method": _text(source.get("method")),
            "params": bound_params(strip_private(params)),
        },
        "display": strip_private(bound_display(display)),
    }


def public_view(widget: Widget, data: dict[str, Any] | None) -> dict[str, Any]:
    """Definition plus rendered data, with owner fields removed."""
    spec = public_spec(widget.spec if isinstance(widget.spec, dict) else {})
    if not spec["title"]:
        spec["title"] = widget.title
    if not spec["kind"]:
        spec["kind"] = widget.kind
    payload = data if isinstance(data, dict) else {}
    return {
        "widget": {
            "id": widget.id,
            "title": widget.title,
            "kind": widget.kind,
            "spec": spec,
        },
        "data": strip_private(payload),
    }


def get(db: Session, widget_id: uuid.UUID) -> Widget | None:
    """Fetch one widget by its unguessable id, ignoring who owns it."""
    return db.query(Widget).filter(Widget.id == widget_id).first()


def list_for_user(db: Session, user_id: uuid.UUID) -> list[Widget]:
    return (
        db.query(Widget)
        .filter(Widget.user_id == user_id)
        .order_by(Widget.pinned.desc(), Widget.sort_order, Widget.created_at.desc())
        .all()
    )


def create(db: Session, user_id: uuid.UUID, spec: WidgetSpec, pinned: bool = False) -> Widget:
    w = Widget(
        user_id=user_id,
        title=spec.title,
        kind=spec.kind,
        spec=spec.model_dump(),
        pinned=pinned,
    )
    db.add(w)
    db.commit()
    db.refresh(w)
    return w


def delete(db: Session, user_id: uuid.UUID, widget_id: uuid.UUID) -> bool:
    w = db.query(Widget).filter(Widget.id == widget_id, Widget.user_id == user_id).first()
    if w is None:
        return False
    db.delete(w)
    db.commit()
    return True


def revoke_public(db: Session, user_id: uuid.UUID, widget_id: uuid.UUID) -> bool:
    """Owner-only. Hide the widget from GET /widgets/{id}/public."""
    w = db.query(Widget).filter(Widget.id == widget_id, Widget.user_id == user_id).first()
    if w is None:
        return False
    w.is_public = False
    db.commit()
    return True


async def render(db: Session, spec: dict[str, Any]) -> dict[str, Any]:
    """Run the data source described in a spec and return its payload."""
    ds = spec.get("data_source") or {}
    service = ds.get("service")
    method = ds.get("method")
    params = bound_params(ds.get("params") or {})

    if service == "comparison" and method == "compare_teams":
        return await comparison_service.compare_teams(
            params.get("team_ids", []), int(params.get("season", 2024))
        )
    if service == "comparison" and method == "compare_team_to_league":
        return await comparison_service.compare_team_to_league(
            params["team_id"], int(params.get("season", 2024))
        )
    if service == "comparison" and method == "compare_players":
        return await comparison_service.compare_players(
            params.get("names", []), int(params.get("season", 2024))
        )
    if service == "stats" and method == "team_aggregate":
        return await stats_service.team_aggregate(
            int(params.get("season", 2024)), params["team_id"]
        )
    if service == "scores" and method == "current":
        games = scores_service.list_current_games(db, limit=int(params.get("limit", 16)))
        return {"games": [_game_dict(g) for g in games]}
    if service == "news" and method == "latest":
        items = news_service.list_news(db, limit=int(params.get("limit", 20)))
        return {"items": [_news_dict(i) for i in items]}
    if service == "odds" and method == "list":
        lines = odds_service.list_odds(db, market=params.get("market"), limit=int(params.get("limit", 50)))
        return {"lines": [_odds_dict(l) for l in lines]}

    return {"error": f"unknown data_source {service}.{method}"}


def _game_dict(g) -> dict[str, Any]:
    return {
        "id": g.id, "season": g.season, "week": g.week,
        "start_time": g.start_time.isoformat() if g.start_time else None,
        "status": g.status, "status_detail": g.status_detail,
        "home_team_id": g.home_team_id, "away_team_id": g.away_team_id,
        "home_score": g.home_score, "away_score": g.away_score,
        "venue": g.venue, "broadcast": g.broadcast,
    }


def _news_dict(n) -> dict[str, Any]:
    return {
        "id": n.id, "source": n.source, "source_label": n.source_label,
        "title": n.title, "summary": n.summary, "link": n.link, "author": n.author,
        "image_url": n.image_url,
        "published_at": n.published_at.isoformat() if n.published_at else None,
    }


def _odds_dict(o) -> dict[str, Any]:
    return {
        "id": o.id, "market": o.market, "event_id": o.event_id,
        "home_team": o.home_team, "away_team": o.away_team,
        "commence_time": o.commence_time.isoformat() if o.commence_time else None,
        "bookmaker": o.bookmaker, "label": o.label, "price": o.price, "point": o.point,
    }
