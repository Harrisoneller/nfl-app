"""The team schedule preview must include finals once games have been played."""
from __future__ import annotations

import pandas as pd
import pytest


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
