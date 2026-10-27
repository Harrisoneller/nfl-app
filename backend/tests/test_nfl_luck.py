"""Luck index: shrunk residuals in, a league z-score out. No market inputs."""
from __future__ import annotations

import pandas as pd
import pytest

from app.services import nfl_luck_service as luck


def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _row(team: str, *, one: float, n: int = 10, extra: float = 0.0) -> dict:
    return {
        "team": team,
        "one_score_luck": one,
        "one_score_n": n,
        "fumble_rec_luck": extra,
        "fumble_n": n,
        "int_luck": extra,
        "int_n": n,
        "rz_td_luck": extra,
        "rz_n": n,
        "fg_luck": extra,
        "fg_n": n,
    }


def test_index_zscores_the_shrunk_residuals_and_does_not_reweight_by_raw_n():
    """Two opposite residuals become +1 and -1. A larger sample does not change that."""
    frame = _frame([
        _row("AAA", one=1.0, n=2),
        _row("BBB", one=-1.0, n=400),
    ])
    bundle = luck.score_season(frame)
    by_team = {row["team"]: row for row in bundle["teams"]}
    assert bundle["title"] == "Regression candidate"
    assert "not a pick" in bundle["disclaimer"]
    assert by_team["AAA"]["score"] == pytest.approx(1.0)
    assert by_team["BBB"]["score"] == pytest.approx(-1.0)
    assert by_team["AAA"]["side"] == "Lucky"
    assert by_team["BBB"]["side"] == "Unlucky"
    assert by_team["AAA"]["rank"] == 1
    assert by_team["BBB"]["rank"] == 2
    assert [row["team"] for row in bundle["teams"]] == ["AAA", "BBB"]
    assert by_team["AAA"]["components"][0]["samples"] == 2
    assert by_team["BBB"]["components"][0]["samples"] == 400


def test_missing_luck_column_is_omitted_and_spec_gaps_stay_listed():
    frame = _frame([
        {"team": "AAA", "one_score_luck": 0.2, "one_score_n": 4},
        {"team": "BBB", "one_score_luck": -0.2, "one_score_n": 4},
    ])
    bundle = luck.score_season(frame)
    omitted_ids = {item["id"] for item in bundle["omitted"]}
    assert "turnover_margin_vs_havoc" in omitted_ids
    assert "points_per_opportunity_vs_success" in omitted_ids
    assert "fumble" in omitted_ids
    assert [item["id"] for item in bundle["components"]] == ["one_score"]
    assert bundle["teams"][0]["team"] == "AAA"
    assert bundle["teams"][0]["score"] == pytest.approx(1.0)


def test_project_plays_drops_market_columns():
    plays = pd.DataFrame({
        "posteam": ["KC"],
        "epa": [0.1],
        "spread": [-3.0],
        "vegas_wp": [0.6],
        "total_line": [44.0],
        "play_id": ["p1"],
    })
    clean = luck._project_plays(plays, {"posteam", "epa"})
    assert list(clean.columns) == ["posteam", "epa"]


def test_service_source_does_not_name_market_inputs():
    from pathlib import Path

    text = Path(luck.__file__).read_text(encoding="utf-8")
    for banned in ("spread_line", "moneyline", "vegas_wp", "closing_spread"):
        assert banned not in text


def test_import_failure_is_logged(monkeypatch):
    import builtins

    events: list[str] = []
    real_import = builtins.__import__

    def _boom(name: str, *args: object, **kwargs: object):
        if name == "features" or name.startswith("features."):
            raise ImportError("features.nfl.defs is not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(luck.log, "error", lambda event, **kwargs: events.append(event))
    monkeypatch.setattr(builtins, "__import__", _boom)
    with pytest.raises(luck.FeatureDefsUnavailable, match="could not be imported"):
        luck._load_defs()
    assert "feature_defs_import_failed" in events


@pytest.mark.asyncio
async def test_missing_defs_is_loud_and_the_job_raises(monkeypatch):
    events: list[str] = []

    def _missing() -> None:
        raise luck.FeatureDefsUnavailable(
            "features.nfl.defs could not be imported. The luck index is unavailable."
        )

    monkeypatch.setattr(luck, "_load_defs", _missing)
    monkeypatch.setattr(luck.log, "error", lambda event, **kwargs: events.append(event))
    bundle = await luck.league_luck(1981)
    assert bundle["teams"] == []
    assert bundle["unavailable"][0]["id"] == "feature_defs"
    assert bundle["cacheable"] is False
    assert "not a pick" in bundle["disclaimer"]
    assert "feature_defs_unavailable" in events
    with pytest.raises(luck.FeatureDefsUnavailable):
        await luck.materialize(object())


@pytest.mark.asyncio
async def test_league_bundle_is_cached(monkeypatch):
    calls = {"n": 0}

    async def _once(season: int) -> dict:
        calls["n"] += 1
        frame = _frame([_row("AAA", one=0.4), _row("BBB", one=-0.4)])
        bundle = luck.score_season(frame)
        bundle["season"] = season
        return bundle

    monkeypatch.setattr(luck, "_load_defs", lambda: object())
    monkeypatch.setattr(luck, "_compute", _once)
    luck._drop(1986)
    first = await luck.league_luck(1986)
    second = await luck.league_luck(1986)
    assert calls["n"] == 1
    assert first["teams"][0]["team"] == "AAA"
    assert second["teams"][0]["score"] == first["teams"][0]["score"]
    view = luck.team_view(first, "BBB")
    assert view["team"]["side"] == "Unlucky"
    assert view["title"] == "Regression candidate"
    assert [row["team"] for row in view["table"]] == ["AAA", "BBB"]
    luck._drop(1986)


def test_luck_columns_are_projected_and_the_fallback_stays_projected():
    import app.adapters.data.nfl_data_py_adapter as adapter

    for col in (
        "ydstogo", "fumble", "fumble_forced", "fumble_recovery_1_team",
        "pass_attempt", "pass_defense_1_player_id", "field_goal_attempt",
        "field_goal_result", "kick_distance", "home_score", "away_score",
    ):
        assert col in adapter.PBP_COLUMNS
    calls: list[list[str] | None] = []

    def _read(url: str, columns: list[str] | None = None, **kwargs: object) -> pd.DataFrame:
        del url, kwargs
        calls.append(columns)
        if columns == adapter.PBP_COLUMNS:
            raise ValueError("missing column fumble")
        return pd.DataFrame({"game_id": ["g"]})

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(adapter.pd, "read_parquet", _read)
    try:
        frame = adapter._import_pbp_lean(2026)
    finally:
        monkeypatch.undo()
    assert list(frame["game_id"]) == ["g"]
    assert calls == [adapter.PBP_COLUMNS, adapter._PBP_CORE]
    assert all(cols is not None for cols in calls)


def test_luck_endpoint_returns_the_regression_candidate(client, monkeypatch):
    frame = _frame([_row("KC", one=0.5), _row("DEN", one=-0.5)])
    bundle = luck.score_season(frame)
    bundle["season"] = 1988

    async def _fake(season: int) -> dict:
        del season
        return bundle

    monkeypatch.setattr(luck, "league_luck", _fake)
    luck._drop(1988)
    response = client.get("/teams/KC/luck?season=1988")
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Regression candidate"
    assert "not a pick" in body["disclaimer"].lower()
    assert body["team"]["team"] == "KC"
    assert body["team"]["side"] == "Lucky"
    assert body["table"][0]["team"] == "KC"
    assert "max-age=300" in response.headers["cache-control"]
    league = client.get("/teams/luck?season=1988")
    assert league.status_code == 200
    assert league.json()["teams"][1]["side"] == "Unlucky"


def test_worker_cron_is_registered_outside_the_full_boot_path():
    from pathlib import Path

    text = Path(luck.__file__).resolve().parents[1].joinpath("jobs", "scheduler.py").read_text()
    assert 'id="team_luck"' in text
    assert "minute=50" in text
