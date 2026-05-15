"""Standalone fantasy analytics.

No league sync. The user pastes a roster (player names or sleeper ids);
we return enriched data + a simple recommendation.

`recommend_start_sit` is a stub that uses the AI layer if available
(see ai_service.fantasy_recommend) — keeping this service free of LLM
imports so it can be called from anywhere.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models.player import Player


def enrich_roster(db: Session, names_or_ids: list[str]) -> list[dict[str, Any]]:
    out = []
    for token in names_or_ids:
        player = db.get(Player, token)
        if player is None:
            player = (
                db.query(Player)
                .filter(or_(Player.full_name == token, Player.full_name.ilike(token)))
                .first()
            )
        if player is None:
            out.append({"query": token, "found": False})
            continue
        out.append({
            "query": token,
            "found": True,
            "player_id": player.id,
            "name": player.full_name,
            "position": player.position,
            "team": player.team_id,
            "status": player.status,
            "injury_status": (player.metadata_json or {}).get("injury_status"),
            "depth_chart": (player.metadata_json or {}).get("depth_chart_order"),
        })
    return out


def trending_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pos_counts: dict[str, int] = {}
    teams: dict[str, int] = {}
    for r in rows:
        if not r.get("found"):
            continue
        pos_counts[r["position"]] = pos_counts.get(r["position"], 0) + 1
        if r["team"]:
            teams[r["team"]] = teams.get(r["team"], 0) + 1
    return {"positions": pos_counts, "team_exposure": teams}
