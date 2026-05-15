"""Scores ingestion + queries.

Two ingestion paths:
1. ESPN live scoreboard → for today's in-progress / upcoming games
2. nfl-data-py `import_schedules(season)` → for historical season schedules

Both feed the same `games` table. ESPN events use ESPN ids; nfl-data-py
games use their `game_id` (e.g. '2024_01_PHI_GB') as the primary key.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..adapters.data.espn import ESPNScoreboardAdapter
from ..adapters.data.nfl_data_py_adapter import NflDataPyAdapter
from ..logging_config import get_logger
from ..models.game import Game
from ..models.team import Team
from ..utils.teams import canonical_team

log = get_logger(__name__)
_nfl_data = NflDataPyAdapter()


async def refresh_scoreboard(db: Session) -> int:
    """Refresh live scoreboard from ESPN."""
    adapter = ESPNScoreboardAdapter()
    try:
        events = await adapter.fetch_current_scoreboard()
    finally:
        await adapter.aclose()

    by_espn_id = {t.espn_id: t for t in db.query(Team).all() if t.espn_id is not None}
    n = 0
    for ev in events:
        home_id = _resolve(by_espn_id, ev.get("home", {}))
        away_id = _resolve(by_espn_id, ev.get("away", {}))
        start = _parse_iso(ev.get("start_time"))
        game = db.get(Game, ev["id"])
        if game is None:
            game = Game(id=ev["id"])
            db.add(game)
        game.season = ev.get("season") or 0
        game.season_type = ev.get("season_type") or 2
        game.week = ev.get("week")
        game.start_time = start
        game.status = ev.get("status") or "scheduled"
        game.status_detail = ev.get("status_detail") or ""
        game.home_team_id = home_id
        game.away_team_id = away_id
        game.home_score = ev.get("home_score")
        game.away_score = ev.get("away_score")
        game.venue = ev.get("venue") or ""
        game.broadcast = ev.get("broadcast") or ""
        game.raw = ev.get("raw") or {}
        n += 1
    db.commit()
    log.info("scoreboard_refreshed", events=n)
    return n


async def refresh_season_schedule(db: Session, season: int) -> int:
    """Pull a full season's schedule from nfl-data-py and upsert."""
    rows = await _nfl_data.schedules(season)
    if not rows:
        return 0
    n = 0
    for row in rows:
        gid = str(row.get("game_id") or "")
        if not gid:
            continue
        home_id = canonical_team(row.get("home_team"))
        away_id = canonical_team(row.get("away_team"))
        gameday = row.get("gameday")
        gametime = row.get("gametime") or "13:00"
        start = _parse_kickoff(gameday, gametime)

        game = db.get(Game, gid)
        if game is None:
            game = Game(id=gid)
            db.add(game)
        game.season = int(row.get("season") or season)
        game.season_type = _season_type(row.get("game_type"))
        game.week = _safe_int(row.get("week"))
        game.start_time = start
        game.home_team_id = home_id
        game.away_team_id = away_id
        game.home_score = _safe_int(row.get("home_score"))
        game.away_score = _safe_int(row.get("away_score"))
        # nfl-data-py doesn't expose live status; if both scores present treat as final
        if game.home_score is not None and game.away_score is not None:
            game.status = "final"
            game.status_detail = "Final"
        else:
            game.status = "scheduled"
            game.status_detail = ""
        game.venue = (row.get("stadium") or "")[:128]
        game.broadcast = (row.get("network") or "")[:64]
        n += 1
    db.commit()
    log.info("season_schedule_refreshed", season=season, games=n)
    return n


def list_current_games(db: Session, limit: int = 32) -> list[Game]:
    return (
        db.query(Game)
        .order_by(Game.start_time.asc().nullslast())
        .limit(limit)
        .all()
    )


def list_team_schedule(db: Session, team_id: str, season: int | None = None) -> list[Game]:
    q = db.query(Game).filter(
        (Game.home_team_id == team_id) | (Game.away_team_id == team_id)
    )
    if season is not None:
        q = q.filter(Game.season == season)
    return q.order_by(Game.start_time.asc().nullslast()).all()


def _resolve(by_espn_id: dict[int, Team], side: dict[str, Any]) -> str | None:
    eid = side.get("espn_id")
    if eid is None:
        return None
    t = by_espn_id.get(int(eid))
    return t.id if t else None


def _parse_iso(s: Any) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _parse_kickoff(date_str: Any, time_str: Any) -> datetime | None:
    if not date_str:
        return None
    try:
        d = str(date_str)[:10]
        t = (str(time_str) or "13:00")[:5]
        return datetime.fromisoformat(f"{d}T{t}:00").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _safe_int(v) -> int | None:
    try:
        return int(v) if v not in (None, "", "null") else None
    except (TypeError, ValueError):
        return None


def _season_type(game_type: Any) -> int:
    gt = str(game_type or "REG").upper()
    if gt.startswith("PRE"):
        return 1
    if gt in ("WC", "DIV", "CON", "SB", "POST"):
        return 3
    return 2
