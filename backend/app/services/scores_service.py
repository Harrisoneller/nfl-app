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


# A stored final score is the one settlement graded. Later feeds must not
# blank it or replace it once the game is settled.
_STORED_FINAL = ("final", "status_final", "complete", "finished", "post")
_INCOMING_LIVE = (
    "final", "in", "live", "in progress", "status_final", "complete", "finished", "post",
)


async def refresh_scoreboard(db: Session) -> int:
    """Refresh the live scoreboard from ESPN, one game per transaction.

    Re-applying the same payload does not write. A failure rolls that game
    back and the rest of the scoreboard still commits.
    """
    adapter = ESPNScoreboardAdapter()
    try:
        events = await adapter.fetch_current_scoreboard()
    finally:
        await adapter.aclose()

    by_espn_id = {
        t.espn_id: t.id for t in db.query(Team).all() if t.espn_id is not None
    }
    db.commit()

    n = 0
    written = 0
    for ev in events:
        gid = str(ev.get("id") or "")
        if not gid:
            continue
        try:
            outcome = _upsert_game(db, gid, _scoreboard_values(ev, by_espn_id))
            db.commit()
            n += 1
            if outcome in ("inserted", "updated"):
                written += 1
        except Exception as exc:  # noqa: BLE001 — one event must not poison the board
            db.rollback()
            log.warning(
                "score_upsert_rolled_back",
                event_id=gid[:64],
                error=str(exc)[:200],
            )
    log.info("scoreboard_refreshed", events=n, written=written)
    return n


async def refresh_season_schedule(db: Session, season: int) -> int:
    """Pull a full season's schedule from nfl-data-py and upsert.

    Each game commits on its own. An identical row is not written again, and
    a failure rolls back only that game.
    """
    rows = await _nfl_data.schedules(season)
    if not rows:
        return 0
    n = 0
    written = 0
    for row in rows:
        gid = str(row.get("game_id") or "")
        if not gid:
            continue
        try:
            outcome = _upsert_game(db, gid, _schedule_values(row, season))
            db.commit()
            n += 1
            if outcome in ("inserted", "updated"):
                written += 1
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            log.warning(
                "schedule_upsert_rolled_back",
                game_id=gid[:64],
                error=str(exc)[:200],
            )
    log.info("season_schedule_refreshed", season=season, games=n, written=written)
    return n


def _scoreboard_values(ev: dict[str, Any], by_espn_id: dict[int, str]) -> dict[str, Any]:
    status = (ev.get("status") or "scheduled").lower()
    # ESPN returns 0 for scheduled/upcoming games — treat as NULL unless live/final.
    if status in ("final", "in", "live", "in progress"):
        home_score = ev.get("home_score")
        away_score = ev.get("away_score")
    else:
        home_score = None
        away_score = None
    return {
        "season": ev.get("season") or 0,
        "season_type": ev.get("season_type") or 2,
        "week": ev.get("week"),
        "start_time": _parse_iso(ev.get("start_time")),
        "status": ev.get("status") or "scheduled",
        "status_detail": ev.get("status_detail") or "",
        "home_team_id": _resolve(by_espn_id, ev.get("home", {})),
        "away_team_id": _resolve(by_espn_id, ev.get("away", {})),
        "home_score": home_score,
        "away_score": away_score,
        "venue": ev.get("venue") or "",
        "broadcast": ev.get("broadcast") or "",
        "raw": ev.get("raw") or {},
    }


def _schedule_values(row: dict[str, Any], season: int) -> dict[str, Any]:
    home_score = _safe_int(row.get("home_score"))
    away_score = _safe_int(row.get("away_score"))
    if home_score is not None and away_score is not None:
        status, detail = "final", "Final"
    else:
        status, detail = "scheduled", ""
    return {
        "season": int(row.get("season") or season),
        "season_type": _season_type(row.get("game_type")),
        "week": _safe_int(row.get("week")),
        "start_time": _parse_kickoff(row.get("gameday"), row.get("gametime") or "13:00"),
        "status": status,
        "status_detail": detail,
        "home_team_id": canonical_team(row.get("home_team")),
        "away_team_id": canonical_team(row.get("away_team")),
        "home_score": home_score,
        "away_score": away_score,
        "venue": (row.get("stadium") or "")[:128],
        "broadcast": (row.get("network") or "")[:64],
    }


def _upsert_game(db: Session, game_id: str, values: dict[str, Any]) -> str:
    """Insert or update one game. Caller commits or rolls back.

    Returns ``inserted``, ``updated``, or ``unchanged``. A settled game keeps
    the score that was graded. A stored final is not blanked by a later
    scheduled payload.
    """
    game = db.get(Game, game_id)
    if game is None:
        db.add(Game(id=game_id, **values))
        return "inserted"

    values = _protect_graded_score(game, values)
    if _values_match(game, values):
        return "unchanged"
    for key, value in values.items():
        setattr(game, key, value)
    return "updated"


def _protect_graded_score(game: Game, values: dict[str, Any]) -> dict[str, Any]:
    stored_final = (game.status or "").lower() in _STORED_FINAL
    incoming_live = (values.get("status") or "").lower() in _INCOMING_LIVE
    locked = game.settlement_status == "settled" or (stored_final and not incoming_live)
    if not locked:
        return values
    kept = dict(values)
    kept["home_score"] = game.home_score
    kept["away_score"] = game.away_score
    kept["status"] = game.status
    kept["status_detail"] = game.status_detail
    return kept


def _values_match(game: Game, values: dict[str, Any]) -> bool:
    return all(_same(getattr(game, key), value) for key, value in values.items())


def _same(current: Any, value: Any) -> bool:
    """Kickoff timestamps round-trip through SQLite without tzinfo.

    The same UTC instant must not count as a new score write.
    """
    if isinstance(current, datetime) or isinstance(value, datetime):
        return _as_utc(current) == _as_utc(value)
    return current == value


def _as_utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


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


def _resolve(by_espn_id: dict[int, str], side: dict[str, Any]) -> str | None:
    eid = side.get("espn_id")
    if eid is None:
        return None
    return by_espn_id.get(int(eid))


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
