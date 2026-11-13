"""Live, near-kickoff, and idle scoreboard poll decisions.

The scheduler tick stays at 30 seconds. ESPN is called every tick while a
game is in progress or inside the lookahead of kickoff, every 180 seconds
when a game is later inside 18 hours, and every 900 seconds when nothing
is live or near. Those floors match the CFB worker.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
        gid="live",
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
        gid="soon",
        start=NOW + timedelta(hours=2),
        status="scheduled",
        last_fetch_at=NOW,
    )
    assert plan["mode"] == "live"
    assert plan["should_fetch"] is True


def test_poll_plan_espn_pre_inside_lookahead_is_live(db):
    plan = _plan(
        db,
        gid="pre",
        start=NOW + timedelta(hours=2),
        status="pre",
        last_fetch_at=NOW,
    )
    assert plan["mode"] == "live"
    assert plan["should_fetch"] is True


def test_poll_plan_lookahead_boundary(db):
    on_window = _plan(
        db,
        gid="edge",
        start=NOW + timedelta(hours=4),
        status="scheduled",
        last_fetch_at=NOW,
    )
    assert on_window["mode"] == "live"
    assert on_window["should_fetch"] is True

    game = db.get(Game, "edge")
    game.start_time = NOW + timedelta(hours=4, seconds=1)
    db.commit()
    just_outside = scores_service.scoreboard_poll_plan(db, now=NOW, last_fetch_at=NOW)
    assert just_outside["mode"] == "near"
    assert just_outside["should_fetch"] is False
    assert just_outside["reason"] == "near_fresh"


def test_poll_plan_recent_start_boundary(db):
    _game(db, gid="started", start=NOW - timedelta(hours=4), status="scheduled")
    live = scores_service.scoreboard_poll_plan(db, now=NOW, last_fetch_at=NOW)
    assert live["mode"] == "live"

    game = db.get(Game, "started")
    game.start_time = NOW - timedelta(hours=4, seconds=1)
    db.commit()
    quiet = scores_service.scoreboard_poll_plan(db, now=NOW, last_fetch_at=NOW)
    assert quiet["mode"] == "idle"
    assert quiet["should_fetch"] is False

    game.status = "in"
    db.commit()
    still_playing = scores_service.scoreboard_poll_plan(db, now=NOW, last_fetch_at=NOW)
    assert still_playing["mode"] == "live"
    assert still_playing["should_fetch"] is True


def test_poll_plan_near_horizon_boundary(db):
    _game(db, gid="later", start=NOW + timedelta(hours=18), status="scheduled")
    near = scores_service.scoreboard_poll_plan(
        db, now=NOW, last_fetch_at=NOW - timedelta(seconds=30),
    )
    assert near["mode"] == "near"
    assert near["should_fetch"] is False

    game = db.get(Game, "later")
    game.start_time = NOW + timedelta(hours=18, seconds=1)
    db.commit()
    idle = scores_service.scoreboard_poll_plan(
        db, now=NOW, last_fetch_at=NOW - timedelta(seconds=30),
    )
    assert idle["mode"] == "idle"
    assert idle["games_in_window"] == 0


def test_poll_plan_near_floor(db):
    _game(db, gid="later", start=NOW + timedelta(hours=8), status="scheduled")
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
    first = scores_service.scoreboard_poll_plan(db, now=NOW, last_fetch_at=None)
    assert first["should_fetch"] is True


def test_poll_plan_finals_and_post_are_idle(db):
    _game(db, gid="done", start=NOW - timedelta(hours=3), status="final")
    _game(db, gid="posted", start=NOW - timedelta(hours=2), status="post")
    plan = scores_service.scoreboard_poll_plan(
        db, now=NOW, last_fetch_at=NOW - timedelta(seconds=60),
    )
    assert plan["mode"] == "idle"
    assert plan["should_fetch"] is False
    assert plan["games_in_window"] == 2


def test_in_progress_beats_a_later_game(db):
    _game(db, gid="live", start=NOW - timedelta(minutes=30), status="in")
    _game(db, gid="later", start=NOW + timedelta(hours=8), status="scheduled")
    plan = scores_service.scoreboard_poll_plan(db, now=NOW, last_fetch_at=NOW)
    assert plan["mode"] == "live"
    assert plan["should_fetch"] is True


def test_final_plus_same_day_game_is_near(db):
    _game(db, gid="done", start=NOW - timedelta(hours=3), status="final")
    _game(db, gid="later", start=NOW + timedelta(hours=8), status="scheduled")
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
    _game(db, gid="live", start=NOW - timedelta(minutes=20), status="in")

    assert await scores_service.refresh_scoreboard(db) == 0
    assert calls["n"] == 1

    forced = await scores_service.refresh_scoreboard(db, force=True)
    assert forced == 0
    assert calls["n"] == 2
