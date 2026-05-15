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
from ..schemas.widget import WidgetSpec
from . import comparison_service, news_service, odds_service, scores_service, stats_service


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


async def render(db: Session, spec: dict[str, Any]) -> dict[str, Any]:
    """Run the data source described in a spec and return its payload."""
    ds = spec.get("data_source") or {}
    service = ds.get("service")
    method = ds.get("method")
    params = ds.get("params") or {}

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
