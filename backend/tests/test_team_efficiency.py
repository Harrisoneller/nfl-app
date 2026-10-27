"""Team efficiency profile wired to features.nfl.defs. No metric math here."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

# Repo root holds features/nfl/defs.py. Backend tests put backend/ on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from features.nfl.defs import CONTRACT, REQUIRED

from app.cache import cache
from app.services import nfl_feature_adapter as features
from app.services import nfl_feature_placeholder as placeholder
from app.services import team_efficiency_service as svc
from app.utils.seasons import current_or_upcoming_season, latest_completed_season

REAL_FILTERS = {
    "implemented": True,
    "garbage_time_excluded": True,
    "wp_low": 0.10,
    "wp_high": 0.90,
    "description": "Only plays with win probability between 10% and 90% are counted.",
    "plays_kept": None,
    "plays_excluded": None,
    "preseason_excluded": True,
}


@pytest.fixture(autouse=True)
def _reset_feature_module():
    features.reset()
    cache.clear()
    yield
    features.reset()
    cache.clear()


def _season_row(**overrides) -> dict:
    row = {
        "season": 2020,
        "team": "AAA",
        "games": 5,
        "n_plays_season": 400,
        "epa_off_adj": 0.11,
        "epa_def_adj": -0.04,
        "sr_off_adj": 0.02,
        "sr_def_adj": -0.01,
        "pass_off_adj": 0.08,
        "pass_def_adj": 0.01,
        "rush_off_adj": 0.03,
        "rush_def_adj": -0.02,
        "sched_strength": 0.132,
        "epa_play_season": 0.07,
        "epa_play_last4": 0.15,
        "epa_play_season_pct": 12.0,
        "success_rate_season_pct": 63.0,
        "pass_epa_season_pct": 70.0,
        "rush_epa_season_pct": 40.0,
        "sched_strength_season_pct": 88.0,
        "early_epa_season": 0.05,
        "early_epa_season_pct": 55.0,
        "early_sr_season": 0.48,
        "passing_epa_season": -0.02,
        "passing_epa_season_pct": 33.0,
        "passing_sr_season": 0.41,
        "epa_allowed_season": 0.01,
        "epa_allowed_last4": -0.03,
        "total_home_score": 27,
        "closing_spread": -3.5,
    }
    row.update(overrides)
    return row


def _games() -> list[dict]:
    return [
        {
            "season": 2020, "week": 2, "game_id": "g2", "team": "AAA",
            "opponent": "BBB", "is_home": 0, "n_plays_last4": 50,
            "epa_play_last4": 0.10, "epa_play_season": 0.20,
            "epa_allowed_last4": 0.30, "epa_allowed_season": 0.40,
        },
        {
            "season": 2020, "week": 5, "game_id": "g5", "team": "AAA",
            "opponent": "CCC", "is_home": 1, "n_plays_last4": 80,
            "epa_play_last4": 0.50, "epa_play_season": 0.25,
            "epa_allowed_last4": 0.05, "epa_allowed_season": 0.10,
        },
    ]


def _bundle(rows: list[dict] | None = None, games: list[dict] | None = None, **kwargs) -> dict:
    return svc.assemble_bundle(
        pd.DataFrame(rows if rows is not None else [_season_row()]),
        pd.DataFrame(games if games is not None else _games()),
        kwargs.get("season", 2020),
        filters=kwargs.get("filters", REAL_FILTERS),
        source=kwargs.get("source", "features.nfl.defs"),
        placeholder=kwargs.get("placeholder", False),
    )


def test_defs_contract_is_the_page_source_and_has_no_market_columns():
    features.reset()
    assert features.using_placeholder() is False
    assert features.source_name() == "features.nfl.defs"
    assert len(CONTRACT) == 86
    for name in (
        "epa_off_adj", "epa_def_adj", "sr_off_adj", "sr_def_adj",
        "pass_off_adj", "rush_off_adj", "sched_strength",
        "epa_play_season", "epa_play_last4", "epa_play_season_pct", "epa_play_last4_pct",
        "early_epa_season", "passing_epa_season", "fumble_rec_luck",
    ):
        assert name in CONTRACT
    assert features.market_columns(CONTRACT) == []
    filters = features.filters()
    assert filters["garbage_time_excluded"] is True
    assert filters["wp_low"] == pytest.approx(0.10)
    assert filters["wp_high"] == pytest.approx(0.90)
    assert "10%" in filters["description"] and "90%" in filters["description"]


def test_adapter_strips_market_columns_before_build_features():
    seen: dict[str, list[str]] = {}

    class _Fake:
        WP_LO = 0.10
        WP_HI = 0.90

        def build_features(self, plays: pd.DataFrame):
            seen["cols"] = list(plays.columns)
            if features.market_columns(plays.columns):
                raise ValueError("market columns reached build_features")
            game = pd.DataFrame({"team": ["AAA"], "week": [1], "epa_play_last4": [0.1]})
            season = pd.DataFrame({"team": ["AAA"], "epa_off_adj": [0.2], "total_home_score": [24]})
            return game, season

    features.use_module(_Fake())
    plays = pd.DataFrame({
        "posteam": ["AAA"],
        "epa": [0.1],
        "spread": [-3.0],
        "moneyline": [-110],
        "total_line": [44.5],
        "total": [47],
        "price": [-105],
        "opener": [3],
        "total_home_score": [21],
    })
    game, season = features.build(plays)
    assert "spread" not in seen["cols"]
    assert "moneyline" not in seen["cols"]
    assert "total_line" not in seen["cols"]
    assert "total" not in seen["cols"]
    assert "price" not in seen["cols"]
    assert "opener" not in seen["cols"]
    assert "total_home_score" in seen["cols"]
    assert "epa" in seen["cols"]
    assert list(game.columns) == ["team", "week", "epa_play_last4"]
    assert "total_home_score" in season.columns
    assert features.filters()["garbage_time_excluded"] is True


def test_service_does_not_implement_epa_or_adjustment():
    text = Path(svc.__file__).read_text(encoding="utf-8")
    for banned in ("epa_adjust", "ridge_adjust", "prepare_plays", "percentile_rank", "schedule_strength("):
        assert banned not in text


def test_page_uses_adjusted_headlines_and_asof_chart():
    profile = svc.project_team(_bundle(), "AAA", 2020)
    overall = profile["splits"][0]
    assert [row["id"] for row in profile["splits"]] == [
        "overall", "pass", "rush", "early_down", "passing_down",
    ]
    assert overall["adjusted"] is True
    assert overall["offense"]["epa"]["value"] == pytest.approx(0.11)
    # One team is the middle of its own league: (0 worse + half a tie) / 1.
    assert overall["offense"]["epa"]["percentile"] == pytest.approx(50.0)
    assert overall["offense"]["epa"]["percentile_of"] is None
    assert overall["offense"]["epa"]["rank"] == 1
    assert overall["offense"]["epa"]["league_size"] == 1
    assert overall["offense"]["success_rate"]["value"] == pytest.approx(0.02)
    assert overall["offense"]["success_rate"]["display"] == "signed"
    assert overall["offense"]["success_rate"]["percentile"] == pytest.approx(50.0)
    assert overall["offense"]["schedule_strength"]["value"] == pytest.approx(0.132)
    assert overall["offense"]["schedule_strength"]["percentile"] == pytest.approx(50.0)
    assert overall["offense"]["schedule_strength"]["rank_sense"] == "better"
    assert overall["defense"]["epa"]["value"] == pytest.approx(-0.04)
    assert overall["defense"]["epa"]["higher_is_better"] is False
    assert overall["defense"]["epa"]["percentile"] == pytest.approx(50.0)
    assert overall["defense"]["success_rate"]["percentile"] == pytest.approx(50.0)
    early = profile["splits"][3]
    assert early["adjusted"] is False
    assert early["offense"]["epa"]["value"] == pytest.approx(0.05)
    assert early["offense"]["epa"]["percentile"] == pytest.approx(55.0)
    assert early["offense"]["epa"]["percentile_of"] == "the season rate"
    assert early["offense"]["epa"]["rank"] is None
    assert early["offense"]["success_rate"]["display"] == "share"
    assert early["defense"]["epa"]["value"] is None
    rolling = profile["rolling"]["offense"]
    assert rolling["as_of_week"] is True
    assert rolling["last4_average"] == pytest.approx(0.15)
    assert rolling["season_average"] == pytest.approx(0.07)
    assert rolling["games"][0]["rolling4"] == pytest.approx(0.10)
    assert rolling["games"][0]["label"] == "Wk 2 @ BBB"
    assert rolling["games"][1]["label"] == "Wk 5 vs CCC"
    assert rolling["games"][1]["season_average"] == pytest.approx(0.25)
    assert profile["rolling"]["defense"]["last4_average"] == pytest.approx(-0.03)
    assert profile["filters"]["garbage_time_excluded"] is True
    stored = profile  # page payload has no raw market cells
    assert "closing_spread" not in str(stored)
    bundle = _bundle()
    assert all("closing_spread" not in row for row in bundle["team_season"])
    assert all(row.get("total_home_score") == 27 for row in bundle["team_season"])


def test_placeholder_omits_metrics_and_does_not_claim_garbage_time():
    features.use_module(placeholder)
    bundle = svc.assemble_bundle(
        placeholder.team_season(pd.DataFrame()),
        placeholder.team_game(pd.DataFrame()),
        2024,
        filters=features.filters(),
        source=features.source_name(),
        placeholder=True,
    )
    profile = svc.project_team(bundle, "KC", 2024)
    assert profile["splits"] == []
    assert profile["filters"]["garbage_time_excluded"] is False
    assert "features/nfl/defs.py" in profile["unavailable"][0]["reason"]
    assert profile["rolling"]["offense"]["games"] == []


def test_unknown_team_is_empty_when_other_teams_have_rows():
    profile = svc.project_team(_bundle(), "ZZZ", 2020)
    assert profile["splits"] == []
    assert "ZZZ" in profile["error"]


def _play_row() -> dict:
    row = {name: 0 for name in REQUIRED}
    row.update({
        "game_id": "g1",
        "season": 2020,
        "week": 1,
        "posteam": "KC",
        "defteam": "BUF",
        "home_team": "KC",
        "away_team": "BUF",
        "epa": 0.4,
        "success": 1,
        "pass": 1,
        "rush": 0,
        "down": 1,
        "ydstogo": 10,
        "yards_gained": 8,
        "wp": 0.55,
        "qb_kneel": 0,
        "qb_spike": 0,
        "special_teams_play": 0,
        "season_type": "REG",
        "spread": -2.5,
    })
    return row


def test_real_build_features_feeds_the_page_without_market_columns():
    game, season = features.build(pd.DataFrame([_play_row()]))
    assert "spread" not in game.columns
    assert "epa_off_adj" in season.columns
    assert set(season["team"]) == {"BUF", "KC"}
    profile = svc.project_team(
        svc.assemble_bundle(
            season, game, 2020,
            filters=features.filters(),
            source=features.source_name(),
            placeholder=False,
        ),
        "KC",
        2020,
    )
    assert profile["filters"]["garbage_time_excluded"] is True
    assert profile["splits"][0]["offense"]["epa"]["value"] is not None
    assert profile["rolling"]["offense"]["as_of_week"] is True
    assert profile["source"] == "features.nfl.defs"


class _Loader:
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame
        self.calls = 0

    async def pbp_df(self, season: int) -> pd.DataFrame:
        del season
        self.calls += 1
        return self.frame


class _Recording:
    def __init__(self) -> None:
        self.calls = 0

    def build_features(self, plays: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        self.calls += 1
        assert len(plays) > 0
        assert "spread" not in plays.columns
        return pd.DataFrame(_games()), pd.DataFrame([_season_row()])


@pytest.mark.asyncio
async def test_real_module_is_cached_across_teams(monkeypatch):
    module = _Recording()
    features.use_module(module)
    loader = _Loader(pd.DataFrame([_play_row()]))
    monkeypatch.setattr(svc, "_pbp_loader", lambda: loader)
    svc._drop_bundle_cache(1983)
    first = await svc.efficiency_profile("AAA", 1983)
    second = await svc.efficiency_profile("AAA", 1983)
    assert loader.calls == 1
    assert module.calls == 1
    assert first["splits"][0]["offense"]["epa"]["percentile"] == pytest.approx(50.0)
    assert second["team_id"] == "AAA"
    assert svc.cache_status(1983) == "hit"


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


def _missing_defs() -> None:
    raise ImportError("features.nfl.defs is not installed")


@pytest.mark.asyncio
async def test_missing_defs_is_loud_and_does_not_download(monkeypatch):
    monkeypatch.setattr(features, "_import_defs", _missing_defs)
    features.reset()

    def _boom() -> None:
        raise AssertionError("a missing feature module must not download play-by-play")

    monkeypatch.setattr(svc, "_pbp_loader", _boom)
    svc._drop_bundle_cache(1981)
    profile = await svc.efficiency_profile("KC", 1981)
    assert profile["source"] == "features.nfl.defs"
    assert profile["filters"]["garbage_time_excluded"] is False
    assert profile["splits"] == []
    assert profile["unavailable"][0]["id"] == "feature_defs"
    assert "could not be imported" in profile["unavailable"][0]["reason"]
    assert svc.cache_status(1981) == "miss"
    again = await svc.efficiency_profile("KC", 1981)
    assert again["unavailable"][0]["id"] == "feature_defs"
    assert svc.cache_status(1981) == "miss"
    with pytest.raises(features.FeatureDefsUnavailable):
        await svc.materialize_profiles(object())


def _rated_row(team: str, *, offense: float, defense: float) -> dict:
    """One team whose adjusted cells all sit on two levels, by side."""
    return _season_row(
        team=team,
        epa_off_adj=offense,
        sr_off_adj=offense,
        pass_off_adj=offense,
        rush_off_adj=offense,
        sched_strength=offense,
        epa_def_adj=defense,
        sr_def_adj=defense,
        pass_def_adj=defense,
        rush_def_adj=defense,
    )


def test_adjusted_percentile_ranks_the_shown_value_with_half_credit_for_ties():
    """26 teams worse and no tie is the 82.8th percentile, 27th of 32."""
    rows = [_rated_row(f"L{index:02d}", offense=-1.0, defense=0.5) for index in range(26)]
    rows.extend(_rated_row(f"H{index:02d}", offense=1.0, defense=-0.5) for index in range(5))
    rows.append(_rated_row("KC", offense=0.059661746737180985, defense=-0.04))
    profile = svc.project_team(_bundle(rows), "KC", 2026)
    by_id = {row["id"]: row for row in profile["splits"]}
    for split_id in ("overall", "pass", "rush"):
        offense = by_id[split_id]["offense"]["epa"]
        defense = by_id[split_id]["defense"]["epa"]
        assert offense["percentile"] == pytest.approx(82.8)
        assert offense["rank"] == 27
        assert offense["league_size"] == 32
        assert offense["percentile_of"] is None
        assert defense["higher_is_better"] is False
        assert defense["percentile"] == pytest.approx(82.8)
        assert defense["rank"] == 27
        assert defense["league_size"] == 32
    assert by_id["overall"]["offense"]["success_rate"]["percentile"] == pytest.approx(82.8)
    assert by_id["overall"]["defense"]["success_rate"]["percentile"] == pytest.approx(82.8)
    assert by_id["overall"]["offense"]["schedule_strength"]["percentile"] == pytest.approx(82.8)

    tied = [
        _rated_row("LOW", offense=0.0, defense=0.2),
        _rated_row("A", offense=0.2, defense=0.0),
        _rated_row("B", offense=0.2, defense=0.0),
    ]
    a = svc.project_team(_bundle(tied), "A", 2026)["splits"][0]["offense"]["epa"]
    low = svc.project_team(_bundle(tied), "LOW", 2026)["splits"][0]["offense"]["epa"]
    # A and B tie: (1 worse + half of 2) / 3 = 66.7. LOW is (half of 1) / 3 = 16.7.
    assert a["percentile"] == pytest.approx(66.7)
    assert a["rank"] == 2
    assert low["percentile"] == pytest.approx(16.7)
    assert low["rank"] == 1
    # Defense is lower-is-better, so 0.0 beats 0.2. The tie is the better rating.
    def_a = svc.project_team(_bundle(tied), "A", 2026)["splits"][0]["defense"]["epa"]
    def_low = svc.project_team(_bundle(tied), "LOW", 2026)["splits"][0]["defense"]["epa"]
    assert def_a["percentile"] == pytest.approx(66.7)
    assert def_low["percentile"] == pytest.approx(16.7)


@pytest.mark.asyncio
async def test_materialize_job_refreshes_completed_and_current_seasons(monkeypatch):
    seen: list[int] = []

    async def _fake(season: int) -> dict:
        seen.append(season)
        return {"team_season_rows": 3, "source": "features.nfl.defs"}

    monkeypatch.setattr(svc, "league_features", _fake)
    monkeypatch.setattr(svc, "_drop_bundle_cache", lambda season: seen.append(-season))
    result = await svc.materialize_profiles(object())
    assert result["rows"] == 6
    assert result["seasons"] == 2
    assert latest_completed_season() in seen
    assert current_or_upcoming_season() in seen
    assert -current_or_upcoming_season() in seen


def test_pbp_projection_covers_the_feature_contract():
    from app.adapters.data.nfl_data_py_adapter import (
        _PBP_FEATURE_OPTIONAL,
        PBP_COLUMNS,
    )

    missing = [col for col in REQUIRED if col != "season" and col not in PBP_COLUMNS]
    assert missing == []
    assert "season" not in PBP_COLUMNS
    optional = (
        "fumble", "fumble_forced", "fumble_recovery_1_team", "pass_attempt",
        "pass_defense_1_player_id", "pass_defense_2_player_id",
        "fixed_drive", "field_goal_attempt", "kick_distance",
        "field_goal_result", "home_score", "away_score",
    )
    assert list(_PBP_FEATURE_OPTIONAL) == list(optional)
    for col in optional:
        assert col in PBP_COLUMNS


def test_missing_optional_column_does_not_load_the_full_parquet(monkeypatch):
    import app.adapters.data.nfl_data_py_adapter as adapter

    calls: list[list[str] | None] = []

    def _read(url: str, columns: list[str] | None = None, **kwargs: object) -> pd.DataFrame:
        del url, kwargs
        calls.append(columns)
        if columns == adapter.PBP_COLUMNS:
            raise ValueError("missing column fumble")
        return pd.DataFrame({"game_id": ["g"]})

    monkeypatch.setattr(adapter.pd, "read_parquet", _read)
    frame = adapter._read_projected_parquet("https://example.test/pbp.parquet")
    assert list(frame["game_id"]) == ["g"]
    assert calls == [adapter.PBP_COLUMNS, adapter._PBP_CORE]
    assert all(cols is not None for cols in calls)


def test_library_fallback_stays_projected(monkeypatch):
    import app.adapters.data.nfl_data_py_adapter as adapter

    calls: list[list[str] | None] = []

    def _import(seasons, columns=None, **kwargs):
        del seasons, kwargs
        calls.append(columns)
        if columns == adapter.PBP_COLUMNS:
            raise ValueError("missing column fumble")
        return pd.DataFrame({"week": [1]})

    monkeypatch.setattr(adapter.nfl, "import_pbp_data", _import)
    frame = adapter._import_pbp_via_library(2026)
    assert list(frame["week"]) == [1]
    assert calls == [adapter.PBP_COLUMNS, adapter._PBP_CORE]


def test_live_season_cache_outlasts_the_worker_gap():
    live = current_or_upcoming_season()
    completed = latest_completed_season()
    assert svc._ttl(live) == svc.LIVE_CACHE_TTL
    assert svc.LIVE_CACHE_TTL > 12 * 60 * 60
    assert svc._l1_ttl(live) == svc.LIVE_L1_TTL
    assert svc._l1_ttl(live) < svc._ttl(live)
    assert svc._ttl(completed) is None
    assert svc._l1_ttl(completed) == svc.CACHE_TTL


def test_build_features_imports_with_backend_pythonpath_only():
    """Railway's root is /backend. The import must work with only that path."""
    backend = Path(__file__).resolve().parents[1]
    packaged = backend / "features" / "nfl" / "defs.py"
    if not packaged.is_file():
        pytest.skip(
            "backend/features/nfl/defs.py is not in this checkout yet. "
            "Rebase onto features/nfl-defs once #31 places the module there."
        )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(backend)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [
            sys.executable, "-c",
            "from features.nfl.defs import build_features; assert callable(build_features)",
        ],
        cwd=str(backend),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_efficiency_endpoint_serves_defs_rows_and_cache_headers(client, monkeypatch):
    features.use_module(_Recording())
    loader = _Loader(pd.DataFrame([_play_row()]))
    monkeypatch.setattr(svc, "_pbp_loader", lambda: loader)
    svc._drop_bundle_cache(1982)
    response = client.get("/teams/AAA/efficiency?season=1982")
    assert response.status_code == 200
    body = response.json()
    assert body["team_id"] == "AAA"
    assert body["season"] == 1982
    # The recording module does not declare WP_LO, so the page must not claim the filter.
    assert body["filters"]["garbage_time_excluded"] is False
    assert body["splits"][0]["offense"]["epa"]["value"] == pytest.approx(0.11)
    assert body["splits"][0]["offense"]["epa"]["percentile"] == pytest.approx(50.0)
    assert body["rolling"]["offense"]["as_of_week"] is True
    assert "closing_spread" not in response.text
    assert response.headers["x-cache-status"] == "miss"
    assert "max-age=300" in response.headers["cache-control"]
    again = client.get("/teams/AAA/efficiency?season=1982")
    assert again.headers["x-cache-status"] == "hit"
    assert again.json()["splits"][0]["id"] == "overall"
