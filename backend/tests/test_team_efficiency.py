"""Team efficiency profile: adapter swap, presentation, cache. No metric math."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from app.cache import cache
from app.services import nfl_feature_adapter as features
from app.services import nfl_feature_placeholder as placeholder
from app.services import team_efficiency_service as svc
from app.utils.seasons import current_or_upcoming_season, latest_completed_season

_MARKET = ("spread", "moneyline", "odds", "total_line", "price", "opener")

REAL_FILTERS = {
    "implemented": True,
    "garbage_time_excluded": True,
    "wp_low": 0.10,
    "wp_high": 0.90,
    "description": "Only plays with win probability between 10% and 90% are counted.",
    "plays_kept": 1000,
    "plays_excluded": 80,
    "preseason_excluded": True,
}


@pytest.fixture(autouse=True)
def _reset_feature_module():
    features.reset()
    cache.clear()
    yield
    features.reset()
    cache.clear()


def _season_row(team: str, **overrides) -> dict:
    row: dict = {"season": 2020, "team": team}
    for split in placeholder.SPLITS:
        row[f"off_{split}_epa"] = 0.0
        row[f"off_{split}_success"] = 0.45
        row[f"off_{split}_plays"] = 100
        row[f"off_{split}_schedule_strength"] = 0.0
        row[f"def_{split}_epa"] = 0.0
        row[f"def_{split}_success"] = 0.45
        row[f"def_{split}_plays"] = 100
        row[f"def_{split}_schedule_strength"] = 0.0
    row.update(overrides)
    return row


def _league_rows() -> list[dict]:
    specs = {
        "AAA": (0.20, 0.50, 0.10, -0.20),
        "BBB": (0.00, 0.40, -0.20, 0.10),
        "CCC": (-0.10, 0.42, 0.00, 0.20),
        "DDD": (0.10, 0.48, 0.05, 0.00),
    }
    rows = []
    for team, (off_epa, success, off_sos, def_epa) in specs.items():
        overrides = {}
        for split in placeholder.SPLITS:
            overrides[f"off_{split}_epa"] = off_epa
            overrides[f"off_{split}_success"] = success
            overrides[f"off_{split}_schedule_strength"] = off_sos
            overrides[f"def_{split}_epa"] = def_epa
        rows.append(_season_row(team, **overrides))
    return rows


def _games() -> list[dict]:
    # Unequal play counts so a plain mean cannot match the chart.
    samples = [(1, 0.0, 10), (2, 0.0, 10), (3, 0.0, 10), (4, 0.0, 10), (5, 1.0, 50)]
    rows = []
    for week, epa, plays in samples:
        rows.append({
            "season": 2020,
            "week": week,
            "game_id": f"AAA-{week}",
            "team": "AAA",
            "opponent": "BBB",
            "location": "away" if week % 2 == 0 else "home",
            "off_epa": epa,
            "def_epa": 0.2,
            "off_plays": plays,
            "def_plays": plays,
            "closing_spread": -3.5,
        })
    return rows


def _bundle(season_rows: list[dict], game_rows: list[dict] | None = None, **kwargs) -> dict:
    return svc.assemble_bundle(
        pd.DataFrame(season_rows),
        pd.DataFrame(game_rows or []),
        kwargs.get("season", 2020),
        filters=kwargs.get("filters", REAL_FILTERS),
        source=kwargs.get("source", "features.nfl.defs"),
        placeholder=kwargs.get("placeholder", False),
    )


def test_placeholder_contract_has_no_market_columns_and_ignores_plays():
    for name, spec in placeholder.CONTRACT.items():
        lowered = name.lower()
        assert not any(token in lowered for token in _MARKET)
        assert {"dtype", "unit", "description"} <= set(spec)
    plays = pd.DataFrame({
        "epa": [9.0],
        "success": [1.0],
        "spread": [3.5],
        "posteam": ["KC"],
    })
    season = placeholder.team_season(plays)
    game = placeholder.team_game(plays)
    assert list(season.columns) == list(placeholder.TEAM_SEASON_COLUMNS)
    assert list(game.columns) == list(placeholder.TEAM_GAME_COLUMNS)
    assert len(season) == 0 and len(game) == 0
    assert placeholder.FILTERS["implemented"] is False
    assert placeholder.FILTERS["garbage_time_excluded"] is False
    assert placeholder.SOURCE == "placeholder"


def test_adapter_prefers_an_injected_module_and_strips_market_columns():
    class _Fake:
        SOURCE = "features.nfl.defs"

        def team_season(self, plays: pd.DataFrame) -> pd.DataFrame:
            assert "epa" in plays.columns
            return pd.DataFrame({
                "team": ["KC"],
                "off_overall_epa": [0.1],
                "closing_spread": [-3.0],
                "moneyline": [-110],
            })

        def team_game(self, plays: pd.DataFrame) -> pd.DataFrame:
            del plays
            return pd.DataFrame({"team": ["KC"], "off_epa": [0.2], "total_line": [44.5]})

    features.use_module(_Fake())
    assert features.using_placeholder() is False
    assert features.source_name() == "features.nfl.defs"
    season = features.team_season(pd.DataFrame({"epa": [1.0]}))
    game = features.team_game(pd.DataFrame())
    assert list(season.columns) == ["team", "off_overall_epa"]
    assert "total_line" not in game.columns
    features.reset()
    assert features.using_placeholder() is True
    assert features.source_name() == "placeholder"


def test_service_does_not_implement_epa_or_adjustment():
    text = Path(svc.__file__).read_text(encoding="utf-8")
    for banned in ("epa_adjust", "ridge_adjust", "prepare_plays", "schedule_strength("):
        assert banned not in text


def test_presentation_ranks_schedule_and_last_four_from_feature_rows():
    bundle = _bundle(_league_rows(), _games())
    profile = svc.project_team(bundle, "AAA", 2020)
    json.dumps(profile)
    overall = profile["splits"][0]
    assert [row["id"] for row in profile["splits"]] == list(placeholder.SPLITS)
    assert overall["approximated"] is False
    assert overall["offense"]["epa"]["value"] == pytest.approx(0.20)
    assert overall["offense"]["epa"]["percentile"] == pytest.approx(87.5)
    assert overall["offense"]["success_rate"]["value"] == pytest.approx(0.50)
    assert overall["offense"]["schedule_strength"]["value"] == pytest.approx(0.10)
    assert overall["offense"]["schedule_strength"]["rank_sense"] == "tougher"
    assert overall["defense"]["epa"]["higher_is_better"] is False
    assert overall["defense"]["epa"]["percentile"] == pytest.approx(87.5)
    bbb = svc.project_team(bundle, "BBB", 2020)
    assert bbb["splits"][0]["offense"]["epa"]["percentile"] == pytest.approx(37.5)
    assert profile["filters"]["garbage_time_excluded"] is True
    assert profile["filters"]["plays_kept"] == 1000
    assert "10%" in profile["filters"]["description"]
    rolling = profile["rolling"]["offense"]
    assert rolling["last4_average"] == pytest.approx(0.625)
    assert rolling["season_average"] == pytest.approx(0.5556)
    assert rolling["games"][-1]["label"] == "Wk 5 vs BBB"
    assert rolling["games"][1]["label"] == "Wk 2 @ BBB"
    assert profile["rolling"]["defense"]["season_average"] == pytest.approx(0.2)
    stored = json.dumps(bundle["team_game"])
    for token in ("closing_spread", "moneyline", "total_line"):
        assert token not in stored


def test_missing_contract_column_is_blank_not_estimated():
    rows = _league_rows()
    for row in rows:
        row.pop("off_overall_schedule_strength")
    profile = svc.project_team(_bundle(rows), "AAA", 2020)
    strength = profile["splits"][0]["offense"]["schedule_strength"]
    assert strength["value"] is None
    assert strength["percentile"] is None
    assert any(item["id"] == "feature_columns" for item in profile["unavailable"])


def test_placeholder_omits_metrics_and_does_not_claim_garbage_time():
    claimed = dict(REAL_FILTERS)
    bundle = _bundle([], [], filters=claimed, source="placeholder", placeholder=True)
    profile = svc.project_team(bundle, "KC", 2024)
    assert profile["splits"] == []
    assert profile["filters"]["garbage_time_excluded"] is False
    assert profile["filters"]["plays_kept"] is None
    assert profile["filters"]["preseason_excluded"] is False
    assert profile["rolling"]["offense"]["games"] == []
    assert "features/nfl/defs.py" in profile["unavailable"][0]["reason"]
    assert "error" not in profile
    assert bundle["team_season"] == []
    assert bundle["team_game"] == []


def test_unknown_team_is_empty_when_the_feature_table_has_other_teams():
    profile = svc.project_team(_bundle(_league_rows()), "ZZZ", 2020)
    assert profile["splits"] == []
    assert "ZZZ" in profile["error"]
    assert profile["filters"]["garbage_time_excluded"] is True


class _Recording:
    SOURCE = "features.nfl.defs"
    FILTERS = REAL_FILTERS
    BINDINGS = placeholder.BINDINGS

    def __init__(self) -> None:
        self.season_calls = 0

    def team_season(self, plays: pd.DataFrame) -> pd.DataFrame:
        self.season_calls += 1
        assert len(plays) > 0
        frame = pd.DataFrame(_league_rows())
        frame["opener_total"] = 47.5
        return frame

    def team_game(self, plays: pd.DataFrame) -> pd.DataFrame:
        del plays
        return pd.DataFrame(_games())


class _Loader:
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame
        self.calls = 0

    async def pbp_df(self, season: int) -> pd.DataFrame:
        del season
        self.calls += 1
        return self.frame


@pytest.mark.asyncio
async def test_real_module_is_cached_and_market_columns_are_not_stored(monkeypatch):
    module = _Recording()
    features.use_module(module)
    loader = _Loader(pd.DataFrame({"posteam": ["AAA"], "epa": [0.1]}))
    monkeypatch.setattr(svc, "_pbp_loader", lambda: loader)
    svc._drop_bundle_cache(1983)
    first = await svc.efficiency_profile("AAA", 1983)
    second = await svc.efficiency_profile("BBB", 1983)
    assert loader.calls == 1
    assert module.season_calls == 1
    assert first["splits"][0]["offense"]["epa"]["value"] == pytest.approx(0.2)
    assert second["team_id"] == "BBB"
    assert svc.cache_status(1983) == "hit"
    bundle = await svc.league_features(1983)
    assert all("opener_total" not in row for row in bundle["team_season"])
    assert all("closing_spread" not in row for row in bundle["team_game"])


@pytest.mark.asyncio
async def test_empty_download_is_not_cached(monkeypatch):
    features.use_module(_Recording())
    loader = _Loader(pd.DataFrame())
    monkeypatch.setattr(svc, "_pbp_loader", lambda: loader)
    svc._drop_bundle_cache(1984)
    first = await svc.efficiency_profile("AAA", 1984)
    assert first["filters"]["garbage_time_excluded"] is False
    assert first["splits"] == []
    assert any(item["id"] == "play_by_play" for item in first["unavailable"])
    assert svc.cache_status(1984) == "miss"
    await svc.efficiency_profile("AAA", 1984)
    assert loader.calls == 2


@pytest.mark.asyncio
async def test_placeholder_path_does_not_download_play_by_play(monkeypatch):
    def _boom() -> None:
        raise AssertionError("placeholder must not download play-by-play")

    monkeypatch.setattr(svc, "_pbp_loader", _boom)
    svc._drop_bundle_cache(1981)
    profile = await svc.efficiency_profile("KC", 1981)
    assert features.using_placeholder() is True
    assert profile["source"] == "placeholder"
    assert profile["filters"]["garbage_time_excluded"] is False
    assert profile["splits"] == []
    assert "calculate those metrics" in profile["unavailable"][0]["reason"]
    assert svc.cache_status(1981) == "hit"


@pytest.mark.asyncio
async def test_materialize_job_refreshes_completed_and_current_seasons(monkeypatch):
    seen: list[int] = []

    async def _fake(season: int) -> dict:
        seen.append(season)
        return {"team_season_rows": 3, "source": "placeholder"}

    monkeypatch.setattr(svc, "league_features", _fake)
    monkeypatch.setattr(svc, "_drop_bundle_cache", lambda season: seen.append(-season))
    result = await svc.materialize_profiles(object())
    assert result["rows"] == 6
    assert result["seasons"] == 2
    assert latest_completed_season() in seen
    assert current_or_upcoming_season() in seen
    assert -current_or_upcoming_season() in seen


def test_efficiency_endpoint_cache_headers_and_honest_placeholder(client, monkeypatch):
    def _boom() -> None:
        raise AssertionError("endpoint placeholder must not download play-by-play")

    monkeypatch.setattr(svc, "_pbp_loader", _boom)
    svc._drop_bundle_cache(1982)
    response = client.get("/teams/KC/efficiency?season=1982")
    assert response.status_code == 200
    body = response.json()
    assert body["team_id"] == "KC"
    assert body["season"] == 1982
    assert body["filters"]["garbage_time_excluded"] is False
    assert body["splits"] == []
    assert "features/nfl/defs.py" in body["unavailable"][0]["reason"]
    assert response.headers["x-cache-status"] == "miss"
    assert "max-age=300" in response.headers["cache-control"]
    again = client.get("/teams/BUF/efficiency?season=1982")
    assert again.status_code == 200
    assert again.headers["x-cache-status"] == "hit"
    assert again.json()["team_id"] == "BUF"
    assert again.json()["filters"]["garbage_time_excluded"] is False
