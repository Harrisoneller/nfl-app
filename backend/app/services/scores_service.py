"""Scores ingestion + queries.

Two ingestion paths:
1. ESPN live scoreboard → for today's in-progress / upcoming games
2. nfl-data-py `import_schedules(season)` → for historical season schedules

Both feed the same `games` table. ESPN events use ESPN ids; nfl-data-py
games use their `game_id` (e.g. '2024_01_PHI_GB') as the primary key.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy.orm import Session

from ..adapters.data.espn import ESPNScoreboardAdapter
from ..adapters.data.nfl_data_py_adapter import NflDataPyAdapter
from ..config import get_settings
from ..logging_config import get_logger
from ..models.game import Game
from ..models.odds_snapshot import OddsSnapshot
from ..models.team import Team
from ..utils.teams import canonical_team
from .closing_line import (
    closing_numbers_from_nflverse,
    closing_numbers_from_sportsbook,
)

log = get_logger(__name__)
_nfl_data = NflDataPyAdapter()


# A stored final score is the one settlement graded. Later feeds must not
# blank it or replace it once the game is settled.
_STORED_FINAL = ("final", "status_final", "complete", "finished", "post")
_INCOMING_LIVE = (
    "final", "in", "live", "in progress", "status_final", "complete", "finished", "post",
)

# ESPN stores state ("pre" / "in" / "post"). nflverse stores "scheduled" / "final".
_LIVE_STATUSES = frozenset({
    "in", "live", "in progress", "in_progress", "halftime",
    "end of period", "end", "delayed", "status_in_progress",
})
# Postponed and suspended are not final: a started game in either state
# stays on the fast tick. Canceled games do not.
_CADENCE_DONE = frozenset({
    "final", "post", "canceled", "cancelled",
    "status_final", "forfeit", "completed",
})
_NEAR_HORIZON = timedelta(hours=18)
_LOOKBACK = timedelta(hours=12)

_last_espn_fetch_at: datetime | None = None
ScorePollMode = Literal["live", "near", "idle"]


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _is_espn_game_id(game_id: object) -> bool:
    """ESPN event ids are numeric. nflverse ids look like ``2026_10_BUF_KC``."""
    return str(game_id or "").isdigit()


def _utc_kickoff(start: datetime | None) -> datetime | None:
    """UTC instant for an ESPN row.

    Postgres timestamptz comes back aware. SQLite drops the zone, so a naive
    value is the same UTC instant. nflverse rows never reach this helper.
    """
    if start is None:
        return None
    if start.tzinfo is None:
        return start.replace(tzinfo=UTC)
    return start.astimezone(UTC)


def _live_plan(reason: str, *, games: int = 0) -> dict[str, Any]:
    return {
        "mode": "live",
        "should_fetch": True,
        "reason": reason,
        "games_in_window": games,
        "age_seconds": None,
    }


def scoreboard_poll_plan(
    db: Session,
    *,
    now: datetime | None = None,
    last_fetch_at: datetime | None = None,
) -> dict[str, Any]:
    """Decide whether this scores tick should call ESPN.

    Kickoff classification uses numeric ESPN ids with timezone-aware UTC
    start times. nflverse rows store an Eastern clock labeled UTC, so they
    are ignored and cannot make the slate idle. A started game that is not
    final stays live with no duration cutoff. Near (a not-yet-started ESPN
    game inside 18 hours) and a quiet trusted slate wait out their floors.
    Empty, unknown, or a process with no successful fetch yet stays live.
    """
    settings = get_settings()
    now = _aware(now) or _now()
    if last_fetch_at is None:
        return _live_plan("no_successful_fetch")
    lookahead = timedelta(hours=float(settings.score_live_lookahead_hours))
    try:
        rows = db.query(Game.id, Game.status, Game.start_time).all()
    except Exception as e:  # noqa: BLE001
        log.debug("scoreboard_poll_plan_failed", error=str(e)[:120])
        return _live_plan("query_failed")
    mode: ScorePollMode = "idle"
    n = 0
    saw_trusted = False
    saw_untrusted = False
    for gid, status, start in rows:
        st = (status or "").strip().lower()
        if not _is_espn_game_id(gid):
            saw_untrusted = True
            continue
        kick = _utc_kickoff(start)
        if kick is None:
            if st not in _CADENCE_DONE:
                saw_untrusted = True
            continue
        saw_trusted = True
        if now - _LOOKBACK <= kick <= now + _NEAR_HORIZON:
            n += 1
        done = st in _CADENCE_DONE
        # kick <= now + lookahead is every started game plus the pre-kick
        # window, so OT, delays, postponements, and suspensions stay live.
        if st in _LIVE_STATUSES or (not done and kick <= now + lookahead):
            mode = "live"
        elif mode != "live" and not done and kick <= now + _NEAR_HORIZON:
            mode = "near"
    if not saw_trusted:
        reason = "untrusted_rows" if saw_untrusted else "empty"
        return _live_plan(reason)
    last = _aware(last_fetch_at)
    age_s = (now - last).total_seconds() if last is not None else None
    if mode == "live":
        should, reason = True, "live_window"
    elif mode == "near":
        floor = int(settings.score_near_refresh_seconds)
        should = age_s is None or age_s >= floor
        reason = "near_due" if should else "near_fresh"
    else:
        floor = int(settings.score_idle_refresh_seconds)
        should = age_s is None or age_s >= floor
        reason = "idle_due" if should else "idle_fresh"
    return {
        "mode": mode,
        "should_fetch": should,
        "reason": reason,
        "games_in_window": n,
        "age_seconds": None if age_s is None else round(age_s, 1),
    }


async def refresh_scoreboard(db: Session, *, force: bool = False) -> int | dict[str, Any]:
    """Refresh the live scoreboard from ESPN, one game per transaction.

    Re-applying the same payload does not write. A failure rolls that game
    back and the rest of the scoreboard still commits. Unless ``force``,
    a near or idle slate skips the network until its floor elapses.
    """
    global _last_espn_fetch_at
    if not force:
        plan = scoreboard_poll_plan(db, last_fetch_at=_last_espn_fetch_at)
        if not plan["should_fetch"]:
            log.debug(
                "scoreboard_refresh_skipped",
                mode=plan["mode"],
                reason=plan["reason"],
                games_in_window=plan["games_in_window"],
            )
            return {
                "status": "skipped_fresh",
                "mode": plan["mode"],
                "reason": plan["reason"],
                "games": 0,
            }
    adapter = ESPNScoreboardAdapter()
    try:
        events = await adapter.fetch_current_scoreboard()
    finally:
        await adapter.aclose()
    # An empty or failed payload must not look like a fresh slate. The
    # exception path never reaches this assignment.
    if not events:
        log.info("scoreboard_refreshed", events=0, written=0)
        return 0
    _last_espn_fetch_at = _now()

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
    values: dict[str, Any] = {
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
    spread, total = closing_numbers_from_nflverse(row.get("spread_line"), row.get("total_line"))
    # Only write a close we actually have. A later snapshot fill can set a
    # missing number without this path blanking one that is already stored.
    if spread is not None:
        values["closing_spread"] = spread
    if total is not None:
        values["closing_total"] = total
    return values


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


_CLOSE_KICKOFF_WINDOW = timedelta(hours=12)


def _snapshot_matches_game(game: Game, snap: OddsSnapshot) -> bool:
    """True when this snapshot is this game, not another meeting of the teams.

    Match the kickoff within ±12 hours, or season, week, and both teams.
    Season and week live on ``raw`` because the snapshot table has neither
    column. A later game's close does not match an earlier kickoff.
    """
    if snap.home_team_id != game.home_team_id or snap.away_team_id != game.away_team_id:
        return False
    game_kick = _as_utc(game.start_time)
    snap_kick = _as_utc(snap.commence_time)
    if (
        game_kick is not None
        and snap_kick is not None
        and abs(game_kick - snap_kick) <= _CLOSE_KICKOFF_WINDOW
    ):
        return True
    raw = snap.raw if isinstance(snap.raw, dict) else {}
    snap_season = raw.get("season")
    snap_week = raw.get("week")
    return (
        game.season is not None
        and game.week is not None
        and snap_season is not None
        and snap_week is not None
        and int(snap_season) == int(game.season)
        and int(snap_week) == int(game.week)
    )


def fill_closing_lines_from_snapshots(db: Session) -> int:
    """Fill missing closes from odds rows already in the database.

    Does not call The Odds API. A sportsbook home spread (negative = home
    favored) is stored as a home margin (positive = home favored). An
    nflverse number already on the game is left alone. A snapshot is used
    only when its kickoff is within ±12 hours of the game, or when its raw
    season and week match the game and both teams match. Every capture used
    as a close, including T4, must have been taken at or before kickoff.
    Among those, T4 wins; otherwise the latest pre-kick capture is used.
    """
    import statistics

    games = (
        db.query(Game)
        .filter((Game.closing_spread.is_(None)) | (Game.closing_total.is_(None)))
        .all()
    )
    written = 0
    for game in games:
        if not game.home_team_id or not game.away_team_id:
            continue
        snaps = [
            s for s in db.query(OddsSnapshot).filter(
                OddsSnapshot.home_team_id == game.home_team_id,
                OddsSnapshot.away_team_id == game.away_team_id,
            ).all()
            if _snapshot_matches_game(game, s)
        ]
        if not snaps:
            continue
        kick = _as_utc(game.start_time)
        pre_kick = []
        if kick is not None:
            for snap in snaps:
                captured = _as_utc(snap.captured_at)
                if captured is not None and captured <= kick:
                    pre_kick.append(snap)
        if not pre_kick:
            continue
        labeled = [
            s for s in pre_kick
            if s.snapshot_label == "T4" and (s.home_spread is not None or s.total is not None)
        ]
        if labeled:
            chosen = labeled
        else:
            latest = max(_as_utc(s.captured_at) for s in pre_kick)
            chosen = [s for s in pre_kick if _as_utc(s.captured_at) == latest]
        spreads = [float(s.home_spread) for s in chosen if s.home_spread is not None]
        totals = [float(s.total) for s in chosen if s.total is not None]
        margin, total = closing_numbers_from_sportsbook(
            statistics.median(spreads) if spreads else None,
            statistics.median(totals) if totals else None,
        )
        changed = False
        if game.closing_spread is None and margin is not None:
            game.closing_spread = margin
            changed = True
        if game.closing_total is None and total is not None:
            game.closing_total = total
            changed = True
        if changed:
            written += 1
    if written:
        db.commit()
    return written


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
