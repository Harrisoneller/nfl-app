"""Team profile schedules must show finals, not the preseason preview."""
from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.game import Game
from app.services import scores_service
from app.services.predictions_service import _schedule_from_db


def _db():
    engine = create_engine("sqlite://", future=True)
    Game.__table__.create(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


def test_espn_post_keeps_the_final_score_and_pre_does_not():
    """ESPN's completed state is ``post``, not ``final``. A 0-0 on ``pre`` is unplayed."""
    final = scores_service._scoreboard_values({
        "status": "post",
        "status_detail": "Final",
        "season": 2026,
        "season_type": 2,
        "week": 11,
        "home_score": 26,
        "away_score": 3,
        "home": {},
        "away": {},
    }, {})
    assert final["home_score"] == 26
    assert final["away_score"] == 3

    scheduled = scores_service._scoreboard_values({
        "status": "pre",
        "season": 2026,
        "week": 11,
        "home_score": 0,
        "away_score": 0,
        "home": {},
        "away": {},
    }, {})
    assert scheduled["home_score"] is None
    assert scheduled["away_score"] is None


def test_team_schedule_is_one_row_and_keeps_the_score():
    db = _db()
    kick = datetime(2026, 11, 19, 1, 15, tzinfo=UTC)
    db.add(Game(
        id="401873100",
        season=2026,
        week=11,
        season_type=2,
        start_time=kick,
        status="post",
        home_team_id="HOU",
        away_team_id="IND",
        home_score=None,
        away_score=None,
        raw={},
    ))
    db.add(Game(
        id="2026_11_IND_HOU",
        season=2026,
        week=11,
        season_type=2,
        start_time=kick,
        status="final",
        home_team_id="HOU",
        away_team_id="IND",
        home_score=26,
        away_score=3,
        raw={},
    ))
    db.commit()
    rows = scores_service.list_team_schedule(db, "HOU", 2026)
    assert len(rows) == 1
    assert rows[0].home_score == 26
    assert rows[0].away_score == 3
    db.close()


def test_slate_keeps_espn_final_when_nflverse_row_has_no_score():
    """A scoreless nflverse duplicate must not hide the ESPN final."""
    db = _db()
    kick = datetime(2026, 11, 19, 1, 15, tzinfo=UTC)
    db.add(Game(
        id="2026_11_IND_HOU",
        season=2026,
        week=11,
        season_type=2,
        start_time=kick,
        status="scheduled",
        home_team_id="HOU",
        away_team_id="IND",
        home_score=None,
        away_score=None,
        raw={},
    ))
    db.add(Game(
        id="401873100",
        season=2026,
        week=11,
        season_type=2,
        start_time=kick,
        status="post",
        home_team_id="HOU",
        away_team_id="IND",
        home_score=26,
        away_score=3,
        raw={},
    ))
    db.commit()
    slate = _schedule_from_db(db, 2026)
    assert len(slate) == 1
    assert int(slate.iloc[0]["home_score"]) == 26
    assert int(slate.iloc[0]["away_score"]) == 3
    db.close()


@pytest.mark.asyncio
async def test_upcoming_schedule_reports_finals_and_leaves_the_future_open(monkeypatch):
    from app.adapters.data.nfl_data_py_adapter import NflDataPyAdapter
    from app.routers import teams as teams_router

    frame = pd.DataFrame([
        {
            "game_id": "2026_01_WAS_PHI",
            "week": 1,
            "home_team": "PHI",
            "away_team": "WAS",
            "home_score": 24,
            "away_score": 22,
            "gameday": "2026-09-13",
            "stadium": "Lincoln Financial Field",
            "network": "NBC",
        },
        {
            "game_id": "2026_11_PIT_PHI",
            "week": 11,
            "home_team": "PHI",
            "away_team": "PIT",
            "home_score": None,
            "away_score": None,
            "gameday": "2026-11-22",
            "stadium": "Lincoln Financial Field",
            "network": "FOX",
        },
    ])

    async def schedules_df(self, season):
        return frame

    async def no_aggs(*_args, **_kwargs):
        return {}

    async def no_weather(_games):
        return {}

    monkeypatch.setattr(NflDataPyAdapter, "schedules_df", schedules_df)
    monkeypatch.setattr(teams_router.analytics_service, "_team_pbp_aggregates", no_aggs)
    monkeypatch.setattr(teams_router.weather_service, "forecasts_for_games", no_weather)

    payload = await teams_router.get_upcoming_season("PHI")
    assert payload["is_upcoming"] is False
    by_week = {g["week"]: g for g in payload["schedule"]}
    assert by_week[1]["played"] is True
    assert by_week[1]["home_score"] == 24
    assert by_week[1]["away_score"] == 22
    assert by_week[11]["played"] is False
    assert by_week[11]["home_score"] is None
    assert by_week[11]["away_score"] is None
