"""Live, near-kickoff, and idle scoreboard poll decisions.

The scheduler tick stays at 30 seconds. Classification uses numeric ESPN
ids with timezone-aware UTC kickoffs. A started game that is not final
stays live with no duration cutoff. A later ESPN game inside 18 hours is
near (180s). A quiet trusted slate is idle (900s). nflverse rows, an empty
board, and a process with no successful fetch stay on the fast tick.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.models.game import Game
from app.models.team import Team
from app.services import scores_service

NOW = datetime(2026, 11, 10, 18, 0, tzinfo=timezone.utc)  # Tuesday


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", future=True)
    Team.__table__.create(bind=engine)
    Game.__table__.create(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    scores_service._last_espn_fetch_at = None
    try:
        yield session
    finally:
        session.close()
        scores_service._last_espn_fetch_at = None
        engine.dispose()


def _game(db, *, gid: str, start: datetime, status: str = "scheduled") -> None:
    db.add(Game(
        id=gid,
        season=2026,
        week=10,
        season_type=2,
        start_time=start,
        status=status,
        raw={},
    ))
    db.commit()


def _plan(db, *, last_fetch_at: datetime | None = None, **game):
    if game:
        _game(db, **game)
    return scores_service.scoreboard_poll_plan(db, now=NOW, last_fetch_at=last_fetch_at)


def test_cadence_defaults_match_cfb():
    settings = Settings()
    assert settings.schedule_scores_seconds == 30
    assert settings.score_live_lookahead_hours == 4.0
    assert settings.score_near_refresh_seconds == 180
    assert settings.score_idle_refresh_seconds == 900


def test_poll_plan_live_in_progress(db):
    plan = _plan(
        db,
        gid="401000001",
        start=NOW - timedelta(hours=1),
        status="in",
        last_fetch_at=NOW,
    )
    assert plan["mode"] == "live"
    assert plan["should_fetch"] is True
    assert plan["reason"] == "live_window"


def test_poll_plan_live_kickoff_soon(db):
    plan = _plan(
        db,
        gid="401000002",
        start=NOW + timedelta(hours=2),
        status="scheduled",
        last_fetch_at=NOW,
    )
    assert plan["mode"] == "live"
    assert plan["should_fetch"] is True


def test_poll_plan_espn_pre_inside_lookahead_is_live(db):
    plan = _plan(
        db,
        gid="401000003",
        start=NOW + timedelta(hours=2),
        status="pre",
        last_fetch_at=NOW,
    )
    assert plan["mode"] == "live"
    assert plan["should_fetch"] is True


def test_poll_plan_lookahead_boundary(db):
    on_window = _plan(
        db,
        gid="401000004",
        start=NOW + timedelta(hours=4),
        status="scheduled",
        last_fetch_at=NOW,
    )
    assert on_window["mode"] == "live"
    assert on_window["should_fetch"] is True

    game = db.get(Game, "401000004")
    game.start_time = NOW + timedelta(hours=4, seconds=1)
    db.commit()
    just_outside = scores_service.scoreboard_poll_plan(db, now=NOW, last_fetch_at=NOW)
    assert just_outside["mode"] == "near"
    assert just_outside["should_fetch"] is False
    assert just_outside["reason"] == "near_fresh"


def test_started_not_final_stays_live_without_cutoff(db):
    _game(db, gid="401000005", start=NOW - timedelta(hours=30), status="scheduled")
    still_open = scores_service.scoreboard_poll_plan(db, now=NOW, last_fetch_at=NOW)
    assert still_open["mode"] == "live"
    assert still_open["should_fetch"] is True

    game = db.get(Game, "401000005")
    game.status = "in"
    db.commit()
    playing = scores_service.scoreboard_poll_plan(db, now=NOW, last_fetch_at=NOW)
    assert playing["mode"] == "live"

    game.status = "final"
    db.commit()
    finished = scores_service.scoreboard_poll_plan(db, now=NOW, last_fetch_at=NOW)
    assert finished["mode"] == "idle"
    assert finished["should_fetch"] is False


def test_poll_plan_near_horizon_boundary(db):
    _game(db, gid="401000006", start=NOW + timedelta(hours=18), status="scheduled")
    near = scores_service.scoreboard_poll_plan(
        db, now=NOW, last_fetch_at=NOW - timedelta(seconds=30),
    )
    assert near["mode"] == "near"
    assert near["should_fetch"] is False

    game = db.get(Game, "401000006")
    game.start_time = NOW + timedelta(hours=18, seconds=1)
    db.commit()
    idle = scores_service.scoreboard_poll_plan(
        db, now=NOW, last_fetch_at=NOW - timedelta(seconds=30),
    )
    assert idle["mode"] == "idle"
    assert idle["games_in_window"] == 0


def test_poll_plan_near_floor(db):
    _game(db, gid="401000006", start=NOW + timedelta(hours=8), status="scheduled")
    fresh = scores_service.scoreboard_poll_plan(
        db, now=NOW, last_fetch_at=NOW - timedelta(seconds=179),
    )
    assert fresh["mode"] == "near"
    assert fresh["should_fetch"] is False
    due = scores_service.scoreboard_poll_plan(
        db, now=NOW, last_fetch_at=NOW - timedelta(seconds=180),
    )
    assert due["mode"] == "near"
    assert due["should_fetch"] is True
    assert due["reason"] == "near_due"


def test_poll_plan_idle_floor(db):
    _game(db, gid="401000099", start=NOW - timedelta(days=2), status="final")
    fresh = scores_service.scoreboard_poll_plan(
        db, now=NOW, last_fetch_at=NOW - timedelta(seconds=899),
    )
    assert fresh["mode"] == "idle"
    assert fresh["should_fetch"] is False
    due = scores_service.scoreboard_poll_plan(
        db, now=NOW, last_fetch_at=NOW - timedelta(seconds=900),
    )
    assert due["mode"] == "idle"
    assert due["should_fetch"] is True
    assert due["reason"] == "idle_due"


def test_poll_plan_finals_and_post_are_idle(db):
    _game(db, gid="401000007", start=NOW - timedelta(hours=3), status="final")
    _game(db, gid="401000008", start=NOW - timedelta(hours=2), status="post")
    plan = scores_service.scoreboard_poll_plan(
        db, now=NOW, last_fetch_at=NOW - timedelta(seconds=60),
    )
    assert plan["mode"] == "idle"
    assert plan["should_fetch"] is False
    assert plan["games_in_window"] == 2


def test_in_progress_beats_a_later_game(db):
    _game(db, gid="401000001", start=NOW - timedelta(minutes=30), status="in")
    _game(db, gid="401000006", start=NOW + timedelta(hours=8), status="scheduled")
    plan = scores_service.scoreboard_poll_plan(db, now=NOW, last_fetch_at=NOW)
    assert plan["mode"] == "live"
    assert plan["should_fetch"] is True


def test_final_plus_same_day_game_is_near(db):
    _game(db, gid="401000007", start=NOW - timedelta(hours=3), status="final")
    _game(db, gid="401000006", start=NOW + timedelta(hours=8), status="scheduled")
    plan = scores_service.scoreboard_poll_plan(
        db, now=NOW, last_fetch_at=NOW - timedelta(seconds=30),
    )
    assert plan["mode"] == "near"
    assert plan["should_fetch"] is False


def test_query_failure_fails_open(db, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(db, "query", boom)
    plan = scores_service.scoreboard_poll_plan(db, now=NOW, last_fetch_at=NOW)
    assert plan == {
        "mode": "live",
        "should_fetch": True,
        "reason": "query_failed",
        "games_in_window": 0,
        "age_seconds": None,
    }


class _Board:
    def __init__(self, calls: dict) -> None:
        self.calls = calls

    async def fetch_current_scoreboard(self):
        self.calls["n"] += 1
        return []

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_refresh_skips_network_when_idle_and_retries_after_floor(db, monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(scores_service, "ESPNScoreboardAdapter", lambda: _Board(calls))
    monkeypatch.setattr(scores_service, "_now", lambda: NOW)
    _game(db, gid="401000099", start=NOW - timedelta(days=2), status="final")
    scores_service._last_espn_fetch_at = NOW - timedelta(seconds=60)

    skipped = await scores_service.refresh_scoreboard(db)
    assert skipped["status"] == "skipped_fresh"
    assert skipped["mode"] == "idle"
    assert calls["n"] == 0

    scores_service._last_espn_fetch_at = NOW - timedelta(seconds=900)
    fetched = await scores_service.refresh_scoreboard(db)
    assert fetched == 0
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_refresh_hits_network_when_live_even_if_just_fetched(db, monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(scores_service, "ESPNScoreboardAdapter", lambda: _Board(calls))
    monkeypatch.setattr(scores_service, "_now", lambda: NOW)
    scores_service._last_espn_fetch_at = NOW
    _game(db, gid="401000001", start=NOW - timedelta(minutes=20), status="in")

    assert await scores_service.refresh_scoreboard(db) == 0
    assert calls["n"] == 1

    forced = await scores_service.refresh_scoreboard(db, force=True)
    assert forced == 0
    assert calls["n"] == 2


def test_eastern_clock_does_not_override_espn_utc(db):
    # 1:00 PM America/New_York on this date is 18:00 UTC. A row stored before
    # the parser fix still has the Eastern clock labeled 13:00Z. The poll
    # plan ignores that nflverse id.
    real_utc = datetime(2026, 11, 15, 13, 0, tzinfo=ZoneInfo("America/New_York")).astimezone(UTC)
    assert scores_service._parse_kickoff("2026-11-15", "13:00") == real_utc
    stale = datetime(2026, 11, 15, 13, 0, tzinfo=UTC)

    now = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
    _game(db, gid="401547320", start=real_utc, status="scheduled")
    _game(db, gid="2026_10_BUF_KC", start=stale, status="scheduled")
    plan = scores_service.scoreboard_poll_plan(db, now=now, last_fetch_at=now)
    # ESPN kickoff is 6h away (near). The mislabeled 13:00Z row is 1h away
    # and would be live if it were trusted.
    assert plan["mode"] == "near"
    assert plan["should_fetch"] is False


def test_nflverse_row_cannot_idle_the_slate(db):
    now = datetime(2026, 11, 15, 15, 0, tzinfo=UTC)
    _game(db, gid="401547320", start=now + timedelta(hours=3), status="pre")
    _game(db, gid="2026_10_BUF_KC", start=now + timedelta(days=3), status="scheduled")
    both = scores_service.scoreboard_poll_plan(db, now=now, last_fetch_at=now)
    assert both["mode"] == "live"
    assert both["should_fetch"] is True

    db.query(Game).filter(Game.id == "401547320").delete()
    db.commit()
    only_nflverse = scores_service.scoreboard_poll_plan(db, now=now, last_fetch_at=now)
    assert only_nflverse["mode"] == "live"
    assert only_nflverse["reason"] == "untrusted_rows"
    assert only_nflverse["should_fetch"] is True


def test_flex_move_outside_near_window_becomes_idle(db):
    _game(db, gid="401547321", start=NOW + timedelta(hours=8), status="scheduled")
    near = scores_service.scoreboard_poll_plan(db, now=NOW, last_fetch_at=NOW)
    assert near["mode"] == "near"
    assert near["should_fetch"] is False

    game = db.get(Game, "401547321")
    game.start_time = NOW + timedelta(hours=19)
    db.commit()
    moved = scores_service.scoreboard_poll_plan(db, now=NOW, last_fetch_at=NOW)
    assert moved["mode"] == "idle"
    assert moved["should_fetch"] is False


def test_postponed_and_suspended_stay_live(db):
    _game(db, gid="401547322", start=NOW - timedelta(hours=8), status="postponed")
    _game(db, gid="401547323", start=NOW - timedelta(hours=10), status="suspended")
    plan = scores_service.scoreboard_poll_plan(db, now=NOW, last_fetch_at=NOW)
    assert plan["mode"] == "live"
    assert plan["should_fetch"] is True


def test_restart_with_quiet_slate_defaults_to_live(db):
    _game(db, gid="401000099", start=NOW - timedelta(days=2), status="final")
    scores_service._last_espn_fetch_at = None
    plan = scores_service.scoreboard_poll_plan(db, now=NOW, last_fetch_at=None)
    assert plan["mode"] == "live"
    assert plan["reason"] == "no_successful_fetch"
    assert plan["should_fetch"] is True


def test_empty_board_stays_live(db):
    plan = scores_service.scoreboard_poll_plan(db, now=NOW, last_fetch_at=NOW)
    assert plan["mode"] == "live"
    assert plan["reason"] == "empty"
    assert plan["should_fetch"] is True


class _Events:
    def __init__(self, events, *, explode: bool = False) -> None:
        self.events = events
        self.explode = explode

    async def fetch_current_scoreboard(self):
        if self.explode:
            raise RuntimeError("espn down")
        return self.events

    async def aclose(self):
        return None


def _live_event() -> dict:
    return {
        "id": "401000777",
        "season": 2026,
        "season_type": 2,
        "week": 10,
        "start_time": "2026-11-15T18:00:00Z",
        "status": "in",
        "status_detail": "Q1",
        "home": {},
        "away": {},
        "home_score": 7,
        "away_score": 0,
        "venue": "",
        "broadcast": "",
        "raw": {},
    }


@pytest.mark.asyncio
async def test_empty_or_failed_payload_does_not_stamp(db, monkeypatch):
    monkeypatch.setattr(scores_service, "_now", lambda: NOW)
    prior = NOW - timedelta(hours=1)
    scores_service._last_espn_fetch_at = prior
    monkeypatch.setattr(scores_service, "ESPNScoreboardAdapter", lambda: _Events([]))
    assert await scores_service.refresh_scoreboard(db) == 0
    assert scores_service._last_espn_fetch_at == prior

    monkeypatch.setattr(
        scores_service, "ESPNScoreboardAdapter", lambda: _Events([], explode=True),
    )
    with pytest.raises(RuntimeError, match="espn down"):
        await scores_service.refresh_scoreboard(db)
    assert scores_service._last_espn_fetch_at == prior

    monkeypatch.setattr(
        scores_service, "ESPNScoreboardAdapter", lambda: _Events([_live_event()]),
    )
    assert await scores_service.refresh_scoreboard(db) == 1
    assert scores_service._last_espn_fetch_at == NOW
