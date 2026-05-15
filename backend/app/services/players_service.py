"""Player CRUD + Sleeper sync."""
from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..adapters.data.sleeper import SleeperAdapter
from ..logging_config import get_logger
from ..models.player import Player

log = get_logger(__name__)

# Sleeper team abbreviation → our team_id (mostly identical).
TEAM_MAP = {
    "JAC": "JAX", "WSH": "WAS", "ARZ": "ARI", "LA": "LAR", "OAK": "LV", "SD": "LAC",
}


def list_players(
    db: Session, *, position: str | None = None, team_id: str | None = None,
    search: str | None = None, limit: int = 100, offset: int = 0,
) -> list[Player]:
    q = db.query(Player)
    if position:
        q = q.filter(Player.position == position.upper())
    if team_id:
        q = q.filter(Player.team_id == team_id.upper())
    if search:
        like = f"%{search}%"
        q = q.filter(Player.full_name.ilike(like))
    return q.order_by(Player.full_name).offset(offset).limit(limit).all()


def get_player(db: Session, player_id: str) -> Player | None:
    return db.get(Player, player_id)


def get_team_roster(db: Session, team_id: str) -> list[Player]:
    return (
        db.query(Player)
        .filter(Player.team_id == team_id.upper(), Player.status == "Active")
        .order_by(Player.position, Player.full_name)
        .all()
    )


async def sync_from_sleeper(db: Session, active_only: bool = True) -> int:
    """Pull NFL players from Sleeper and upsert.

    Sleeper's `/players/nfl` returns ~11,400 records including every player
    who's ever been on a roster. With `active_only=True` (default) we filter
    to those currently on a team — usually ~2,000 — which makes the sync
    ~5x faster and avoids polluting search results with retired players.
    """
    adapter = SleeperAdapter()
    try:
        all_players = await adapter.fetch_all_players()
    finally:
        await adapter.aclose()

    n = 0
    for pid, raw in all_players.items():
        if not raw or raw.get("position") is None:
            continue
        team_raw = raw.get("team")
        if active_only and not team_raw:
            continue
        team_id = TEAM_MAP.get(team_raw, team_raw) if team_raw else None

        p = db.get(Player, pid)
        if p is None:
            p = Player(id=pid)
            db.add(p)
        p.gsis_id = raw.get("gsis_id")
        p.espn_id = _safe_int(raw.get("espn_id"))
        p.full_name = (raw.get("full_name") or
                       f"{raw.get('first_name','')} {raw.get('last_name','')}".strip())
        p.position = (raw.get("position") or "")[:8]
        p.team_id = team_id
        p.jersey_number = _safe_int(raw.get("number"))
        p.age = _safe_int(raw.get("age"))
        p.height = (raw.get("height") or None)
        p.weight = _safe_int(raw.get("weight"))
        p.college = (raw.get("college") or None)
        p.status = (raw.get("status") or "")[:32]
        p.metadata_json = {
            "fantasy_positions": raw.get("fantasy_positions"),
            "depth_chart_position": raw.get("depth_chart_position"),
            "depth_chart_order": raw.get("depth_chart_order"),
            "injury_status": raw.get("injury_status"),
            "injury_body_part": raw.get("injury_body_part"),
            "injury_notes": raw.get("injury_notes"),
            "years_exp": raw.get("years_exp"),
        }
        n += 1
    db.commit()
    log.info("players_synced", count=n)
    return n


def _safe_int(v) -> int | None:
    try:
        return int(v) if v not in (None, "", "null") else None
    except (TypeError, ValueError):
        return None
