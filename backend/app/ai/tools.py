"""Tool definitions exposed to the LLM, plus a `run_tool` dispatcher.

The tool schema is OpenAI-compatible (Grok speaks the same dialect). The
dispatcher resolves each tool name to the appropriate service call so the
AI uses the same code paths as the REST API.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from ..schemas.widget import WidgetSpec
from ..services import (
    comparison_service,
    news_service,
    odds_service,
    players_service,
    scores_service,
    stats_service,
    teams_service,
    widget_service,
)


def get_tool_definitions() -> list[dict[str, Any]]:
    """Return OpenAI/Grok-style tool/function definitions."""
    return [
        _fn(
            "get_current_scoreboard",
            "List today's NFL games with status and scores.",
            {"type": "object", "properties": {"limit": {"type": "integer", "default": 16}}, "additionalProperties": False},
        ),
        _fn(
            "get_team",
            "Get a team's profile (name, division, colors).",
            {
                "type": "object",
                "properties": {"team_id": {"type": "string", "description": "Team abbreviation, e.g. 'PHI'"}},
                "required": ["team_id"],
            },
        ),
        _fn(
            "get_team_roster",
            "Get a team's active roster.",
            {
                "type": "object",
                "properties": {"team_id": {"type": "string"}},
                "required": ["team_id"],
            },
        ),
        _fn(
            "get_team_stats",
            "Get a team's aggregated season stats.",
            {
                "type": "object",
                "properties": {
                    "team_id": {"type": "string"},
                    "season": {"type": "integer"},
                },
                "required": ["team_id", "season"],
            },
        ),
        _fn(
            "search_players",
            "Search for players by partial name. Returns up to 20.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "position": {"type": "string"},
                    "team_id": {"type": "string"},
                },
                "required": ["query"],
            },
        ),
        _fn(
            "get_player_stats",
            "Get a player's season stats by full name.",
            {
                "type": "object",
                "properties": {
                    "full_name": {"type": "string"},
                    "season": {"type": "integer"},
                },
                "required": ["full_name", "season"],
            },
        ),
        _fn(
            "compare_teams",
            "Compare aggregated season stats across N teams.",
            {
                "type": "object",
                "properties": {
                    "team_ids": {"type": "array", "items": {"type": "string"}},
                    "season": {"type": "integer"},
                },
                "required": ["team_ids", "season"],
            },
        ),
        _fn(
            "compare_team_to_league",
            "Show a team's rank vs the rest of the league for each metric.",
            {
                "type": "object",
                "properties": {
                    "team_id": {"type": "string"},
                    "season": {"type": "integer"},
                },
                "required": ["team_id", "season"],
            },
        ),
        _fn(
            "compare_players",
            "Compare season stats across N players (by full name).",
            {
                "type": "object",
                "properties": {
                    "names": {"type": "array", "items": {"type": "string"}},
                    "season": {"type": "integer"},
                },
                "required": ["names", "season"],
            },
        ),
        _fn(
            "get_latest_news",
            "Top NFL news items from RSS + Reddit.",
            {
                "type": "object",
                "properties": {"limit": {"type": "integer", "default": 20}, "source": {"type": "string"}},
            },
        ),
        _fn(
            "get_odds",
            "Sportsbook odds. Optionally filter by market (h2h, spreads, totals, outrights).",
            {
                "type": "object",
                "properties": {
                    "market": {"type": "string"},
                    "limit": {"type": "integer", "default": 50},
                },
            },
        ),
        _fn(
            "build_widget",
            "Construct a WidgetSpec the frontend can render. Use when the user wants a view/visualization.",
            {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": [
                            "stat_card", "comparison_table", "list", "scoreboard",
                            "bar_chart", "line_chart", "table", "news_list", "odds_table",
                        ],
                    },
                    "service": {"type": "string"},
                    "method": {"type": "string"},
                    "params": {"type": "object"},
                    "description": {"type": "string"},
                },
                "required": ["title", "kind", "service", "method"],
            },
        ),
    ]


def _fn(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    }


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class ToolRunner:
    """Bound to a DB session and current user; produces the tool_runner the LLM uses."""

    def __init__(self, db: Session, user_id: uuid.UUID) -> None:
        self.db = db
        self.user_id = user_id
        self.last_widget: WidgetSpec | None = None

    async def __call__(self, name: str, args: dict[str, Any]) -> Any:
        if name == "get_current_scoreboard":
            games = scores_service.list_current_games(self.db, limit=int(args.get("limit", 16)))
            return [widget_service._game_dict(g) for g in games]

        if name == "get_team":
            t = teams_service.get_team(self.db, args["team_id"])
            return _team_dict(t) if t else None

        if name == "get_team_roster":
            roster = players_service.get_team_roster(self.db, args["team_id"])
            return [_player_dict(p) for p in roster]

        if name == "get_team_stats":
            return await stats_service.team_aggregate(int(args["season"]), args["team_id"])

        if name == "search_players":
            players = players_service.list_players(
                self.db,
                search=args["query"],
                position=args.get("position"),
                team_id=args.get("team_id"),
                limit=20,
            )
            return [_player_dict(p) for p in players]

        if name == "get_player_stats":
            return await stats_service.player_aggregate(int(args["season"]), args["full_name"])

        if name == "compare_teams":
            return await comparison_service.compare_teams(args["team_ids"], int(args["season"]))

        if name == "compare_team_to_league":
            return await comparison_service.compare_team_to_league(args["team_id"], int(args["season"]))

        if name == "compare_players":
            return await comparison_service.compare_players(args["names"], int(args["season"]))

        if name == "get_latest_news":
            items = news_service.list_news(
                self.db, limit=int(args.get("limit", 20)), source=args.get("source")
            )
            return [widget_service._news_dict(i) for i in items]

        if name == "get_odds":
            lines = odds_service.list_odds(
                self.db, market=args.get("market"), limit=int(args.get("limit", 50))
            )
            return [widget_service._odds_dict(l) for l in lines]

        if name == "build_widget":
            spec = WidgetSpec(
                title=args["title"],
                kind=args["kind"],
                description=args.get("description", ""),
                data_source={
                    "service": args["service"],
                    "method": args["method"],
                    "params": args.get("params") or {},
                },
                display={},
            )
            self.last_widget = spec
            # Save the widget so it shows up on the dashboard
            saved = widget_service.create(self.db, self.user_id, spec, pinned=False)
            return {
                "ok": True,
                "widget_id": str(saved.id),
                "spec": spec.model_dump(),
                "message": f"Widget '{spec.title}' created and added to your dashboard.",
            }

        raise ValueError(f"unknown tool: {name}")


def _team_dict(t) -> dict[str, Any]:
    return {
        "id": t.id, "espn_id": t.espn_id, "market": t.market, "name": t.name,
        "full_name": t.full_name, "conference": t.conference, "division": t.division,
        "primary_color": t.primary_color, "secondary_color": t.secondary_color,
        "logo_url": t.logo_url,
    }


def _player_dict(p) -> dict[str, Any]:
    return {
        "id": p.id, "name": p.full_name, "position": p.position, "team": p.team_id,
        "jersey": p.jersey_number, "status": p.status,
        "injury_status": (p.metadata_json or {}).get("injury_status"),
    }
