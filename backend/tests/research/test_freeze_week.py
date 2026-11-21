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
