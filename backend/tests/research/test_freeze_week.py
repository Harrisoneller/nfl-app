"""The totals freeze on a synthetic week. It does not score the cache."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pandas as pd
import pytest

from research.nfl_drive_sim.freeze_week import (
    FORMAT_VERSION,
    FREEZE_COLUMNS,
    WINDOW1_N,
    FreezeError,
    FreezeLocked,
    assert_freeze_in_time,
    assert_history_covers,
    counts_toward_drop,
    first_valid_freeze,
    freeze_week,
    load_window1,
    prereg_is_locked,
    projection_frame,
    refuse_started_game,
    schedule_change,
    select_week_games,
    write_freeze,
)

ROOT = Path(__file__).resolve().parents[2] / "research" / "nfl_drive_sim"
WINDOW = ROOT / "outputs" / "totals" / "window1_game_ids.csv"
WINDOW_SHA = "1a935f89a6ae60612c530493095adc1bcb93b99e5059176766dfee7502aa82ee"


def test_prereg_is_still_a_draft_and_the_freeze_refuses():
    assert prereg_is_locked() is False
    with pytest.raises(FreezeLocked):
        freeze_week(2026, 11, "2026-11-22")


def test_week_11_remainder_drops_thursday():
    games = pd.DataFrame([
        {"season": 2026, "week": 11, "game_type": "REG", "game_id": "2026_11_IND_HOU",
         "gameday": "2026-11-19", "gametime": "20:15", "home_team": "HOU", "away_team": "IND"},
        {"season": 2026, "week": 11, "game_type": "REG", "game_id": "2026_11_MIA_BUF",
         "gameday": "2026-11-22", "gametime": "13:00", "home_team": "BUF", "away_team": "MIA"},
        {"season": 2026, "week": 12, "game_type": "REG", "game_id": "2026_12_GB_LA",
         "gameday": "2026-11-25", "gametime": "20:00", "home_team": "LA", "away_team": "GB"},
    ])
    slate, cutoff = select_week_games(games, 2026, 11, "2026-11-22")
    assert list(slate["game_id"]) == ["2026_11_MIA_BUF"]
    assert cutoff == pd.Timestamp("2026-11-22 18:00", tz="UTC")
    assert "2026_11_IND_HOU" not in set(slate["game_id"])


def test_window_pin_is_121_games_and_includes_ari_kc(tmp_path):
    text = WINDOW.read_text()
    assert hashlib.sha256(text.encode()).hexdigest() == WINDOW_SHA
    frame = load_window1(WINDOW)
    assert len(frame) == WINDOW1_N == 121
    assert "2026_11_ARI_KC" in set(frame["game_id"].astype(str))
    assert "2026_11_IND_HOU" not in set(frame["game_id"].astype(str))
    assert list(frame.columns[:3]) == ["game_id", "kickoff_et", "week"]
    broken = tmp_path / "window1_game_ids.csv"
    broken.write_text(text.replace("2026_11_ARI_KC", "2026_11_ARI_XX"))
    with pytest.raises(FreezeError, match="window1 hash"):
        load_window1(broken)


def test_a_missed_freeze_is_not_backfilled():
    first = pd.Timestamp("2026-11-22 18:00", tz="UTC")
    later = pd.Timestamp("2026-11-23 01:15", tz="UTC")
    with pytest.raises(FreezeError, match="never backfilled"):
        assert_freeze_in_time([first, later], first)
    with pytest.raises(FreezeError, match="already past kickoff"):
        refuse_started_game(later, pd.Timestamp("2026-11-23 02:00", tz="UTC"))
    assert_freeze_in_time([first, later], pd.Timestamp("2026-11-22 17:59", tz="UTC"))
    refuse_started_game(first, pd.Timestamp("2026-11-22 17:59", tz="UTC"))


def test_postponement_rules():
    assert schedule_change(11, 11, cancelled=True) == "excluded"
    assert counts_toward_drop("excluded") is False
    assert schedule_change(11, 11, cancelled=False) == "same_week"
    assert schedule_change(11, 13, cancelled=False) == "other_week"
    assert schedule_change(11, 19, cancelled=False) == "excluded"
    kick = pd.Timestamp("2026-11-22 18:00", tz="UTC")
    early = pd.Timestamp("2026-11-22 16:00", tz="UTC")
    late = pd.Timestamp("2026-11-22 19:00", tz="UTC")
    chosen = first_valid_freeze([(late, kick), (early, kick)])
    assert chosen == early
    assert first_valid_freeze([(late, kick)]) is None


def test_a_score_or_a_market_column_cannot_be_a_projection():
    with pytest.raises(FreezeError):
        projection_frame([{"game_id": "g", "crps_total": 1.0}])
    with pytest.raises(FreezeError):
        projection_frame([{"game_id": "g", "spread_line": -3.0}])
    kept = projection_frame([{"game_id": "g", "v5_total": 44.0, "ypp_on": False}])
    assert list(kept.columns) == ["game_id", "v5_total", "ypp_on"]


def test_a_hole_before_the_cutoff_aborts():
    schedule = pd.DataFrame([
        {"game_id": "played", "gameday": "2026-11-19", "gametime": "20:15",
         "home_score": 20, "away_score": 17},
        {"game_id": "hole", "gameday": "2026-11-20", "gametime": "20:15",
         "home_score": None, "away_score": None},
    ])
    plays = pd.DataFrame({"game_id": ["played", "hole"]})
    cutoff = pd.Timestamp("2026-11-22 13:00", tz="America/New_York")
    with pytest.raises(FreezeError, match="no score"):
        assert_history_covers(schedule, plays, cutoff)


def test_format_is_versioned_and_the_prereg_names_the_scoring_source():
    assert FORMAT_VERSION == "totals-freeze-v1"
    assert FREEZE_COLUMNS[0] == "format_version"
    assert FREEZE_COLUMNS.index("sim_margin_mean") < FREEZE_COLUMNS.index("sim_total_mean")
    text = (ROOT / "TOTALS_PREREG.md").read_text()
    assert "Status: draft" in text
    assert "home minus away" in text
    assert "games.json" in text
    assert "pull request 54" in text
    assert "all or none" in text
    assert "freeze_timestamp_utc" in text
    assert "closing line value" in text


def _locked(tmp_path) -> Path:
    path = tmp_path / "TOTALS_PREREG.md"
    path.write_text("Status: locked\n")
    return path


def _projection(game) -> dict:
    return {
        "format_version": FORMAT_VERSION,
        "game_id": str(game.game_id),
        "season": 2026,
        "week": int(game.week),
        "kickoff_et": pd.Timestamp(game.kickoff_et).isoformat(),
        "home_team": game.home_team,
        "away_team": game.away_team,
        "home_qb": "h",
        "away_qb": "a",
        "drop_reason": "",
        "sim_margin_mean": 1.0,
        "sim_total_mean": 44.0,
        "sim_margin_pmf": "1",
        "sim_total_pmf": "1",
        "v5_margin": 1.0,
        "v5_total": 44.0,
        "v5_sigma_margin": 10.0,
        "v5_sigma_total": 10.0,
        "n_sims": 10000,
        "seed": 1,
        "ypp_on": False,
    }


def _project(slate, _cutoff):
    rows = [_projection(game) for game in slate.itertuples(index=False)]
    features = pd.DataFrame({"game_id": [row["game_id"] for row in rows], "note": "synthetic"})
    return rows, features


def test_write_freeze_never_overwrites(tmp_path):
    row = {
        "format_version": FORMAT_VERSION,
        "game_id": "G",
        "season": 2026,
        "week": 11,
        "kickoff_et": "2026-11-22T18:00:00+00:00",
        "home_team": "BUF",
        "away_team": "MIA",
        "home_qb": "h",
        "away_qb": "a",
        "drop_reason": "",
        "sim_margin_mean": 1.0,
        "sim_total_mean": 44.0,
        "sim_margin_pmf": "1",
        "sim_total_pmf": "1",
        "v5_margin": 1.0,
        "v5_total": 44.0,
        "v5_sigma_margin": 10.0,
        "v5_sigma_total": 10.0,
        "n_sims": 10000,
        "seed": 1,
        "ypp_on": False,
    }
    features = pd.DataFrame({"game_id": ["G"], "note": ["synthetic"]})
    root = tmp_path / "freezes"
    dest = root / "2026_w11_first"
    write_freeze(dest, [row], features, {"freeze_timestamp_utc": "2026-11-01T00:00:00Z"}, search_root=root)
    original = (dest / "projections.csv").read_bytes()
    with pytest.raises(FreezeError, match="already exists"):
        write_freeze(dest, [row], features, {"freeze_timestamp_utc": "2026-11-02T00:00:00Z"}, search_root=root)
    assert (dest / "projections.csv").read_bytes() == original
    other = root / "2026_w11_second"
    with pytest.raises(FreezeError, match="already exists"):
        write_freeze(other, [row], features, {"freeze_timestamp_utc": "2026-11-02T00:00:00Z"}, search_root=root)
    assert not other.exists()


def test_a_moved_game_is_frozen_in_the_week_it_is_played(tmp_path):
    window = pd.DataFrame({
        "game_id": ["M", "B"],
        "kickoff_et": ["2026-11-22T13:00:00-05:00", "2026-11-22T13:00:00-05:00"],
        "week": [11, 11],
    })
    schedule = pd.DataFrame({
        "game_id": ["M", "B"],
        "season": [2026, 2026],
        "week": [13, 11],
        "game_type": ["REG", "REG"],
        "kickoff_et": ["2026-11-29T13:00:00-05:00", "2026-11-22T13:00:00-05:00"],
        "home_team": ["BUF", "DET"],
        "away_team": ["KC", "CHI"],
        "cancelled": [False, False],
    })
    now = pd.Timestamp("2026-11-01T00:00:00Z")
    prereg = _locked(tmp_path)
    freeze_week(
        2026, 13, prereg_path=prereg, window=window, schedule=schedule,
        out_root=tmp_path / "out", projector=_project, now=now,
    )
    freeze_week(
        2026, 11, prereg_path=prereg, window=window, schedule=schedule,
        out_root=tmp_path / "out", projector=_project, now=now,
    )
    week_13 = next((tmp_path / "out").glob("2026_w13_*"))
    week_11 = next((tmp_path / "out").glob("2026_w11_*"))
    moved = pd.read_csv(week_13 / "projections.csv")
    stayed = pd.read_csv(week_11 / "projections.csv")
    assert list(moved["game_id"]) == ["M"]
    assert int(moved["week"].iloc[0]) == 13
    assert list(stayed["game_id"]) == ["B"]
    assert list(pd.read_csv(week_11 / "exclusions.csv")["reason"]) == ["moved"]
    before = (week_13 / "projections.csv").read_bytes()
    with pytest.raises(FreezeError, match="already exists"):
        freeze_week(
            2026, 13, prereg_path=prereg, window=window, schedule=schedule,
            out_root=tmp_path / "out", projector=_project, now=now,
        )
    assert (week_13 / "projections.csv").read_bytes() == before


def test_a_cancelled_game_is_excluded_and_the_rest_freeze(tmp_path):
    window = pd.DataFrame({
        "game_id": ["C", "D", "X"],
        "kickoff_et": ["2026-11-29T13:00:00-05:00"] * 3,
        "week": [13, 13, 13],
    })
    schedule = pd.DataFrame({
        "game_id": ["C", "D"],
        "season": [2026, 2026],
        "week": [13, 13],
        "game_type": ["REG", "REG"],
        "kickoff_et": ["2026-11-29T13:00:00-05:00", "2026-11-29T16:00:00-05:00"],
        "home_team": ["BUF", "DET"],
        "away_team": ["KC", "CHI"],
        "cancelled": [True, False],
    })
    freeze_week(
        2026, 13, prereg_path=_locked(tmp_path), window=window, schedule=schedule,
        out_root=tmp_path / "out", projector=_project, now=pd.Timestamp("2026-11-01T00:00:00Z"),
    )
    folder = next((tmp_path / "out").glob("2026_w13_*"))
    assert list(pd.read_csv(folder / "projections.csv")["game_id"]) == ["D"]
    logged = pd.read_csv(folder / "exclusions.csv")
    assert set(zip(logged["game_id"], logged["reason"], strict=True)) == {("C", "cancelled"), ("X", "missing")}


def test_freeze_source_does_not_import_the_served_model_or_a_market_path():
    path = ROOT / "freeze_week.py"
    text = path.read_text()
    tree = ast.parse(text)
    banned = ("team_ratings_v5", "app.services", "build_week_model", "market_ratings", "backtest_v5")
    for name in banned:
        assert name not in text
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "app.services" not in node.module
