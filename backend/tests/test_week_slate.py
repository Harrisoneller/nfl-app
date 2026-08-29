"""Week slate flattening — model vs market fields the /week page reads."""
import pandas as pd

from app.services.predictions_service import (
    _flatten_slate_game,
    _next_unplayed_reg_week,
    _reg_weeks_from_sched,
)


def test_flatten_lifts_market_and_edge():
    row = _flatten_slate_game({
        "id": "2026_01_NE_SEA",
        "season": 2026,
        "week": 1,
        "gameday": "2026-09-10",
        "gametime": "20:20",
        "home_team_id": "SEA",
        "away_team_id": "NE",
        "home_score": None,
        "away_score": None,
        "home_elo": 1610.4,
        "away_elo": 1480.1,
        "prediction": {
            "predicted_spread": -4.5,
            "predicted_total": 43.0,
            "predicted_home_score": 23.8,
            "predicted_away_score": 19.2,
            "home_win_prob": 0.64,
            "confidence_tier": "medium",
            "game_script": "Defensive grind",
            "model_only": {"predicted_spread": -6.2},
            "market": {"spread_home": -3.5, "total": 44.5},
            "edge": {"spread": -1.0, "total": -1.5},
            "distribution": {"margin_interval_80": [-18.0, 9.0]},
        },
    })
    assert row["model_spread"] == -4.5
    assert row["market_spread"] == -3.5
    assert row["spread_edge"] == -1.0
    assert row["market_total"] == 44.5
    assert row["elo_gap"] == 130.3
    assert row["model_raw_spread"] == -6.2
    assert row["distribution"]["margin_interval_80"] == [-18.0, 9.0]


def test_flatten_missing_prediction_does_not_crash():
    row = _flatten_slate_game({
        "id": "x",
        "home_team_id": "KC",
        "away_team_id": "BUF",
    })
    assert row["model_spread"] is None
    assert row["market_spread"] is None
    assert row["elo_gap"] is None


def test_next_unplayed_reg_week_skips_preseason():
    sched = pd.DataFrame([
        {"week": 1, "game_type": "PRE", "home_score": None, "away_score": None},
        {"week": 1, "game_type": "REG", "home_score": None, "away_score": None},
        {"week": 2, "game_type": "REG", "home_score": None, "away_score": None},
    ])
    assert _next_unplayed_reg_week(sched) == 1


def test_next_unplayed_reg_week_advances_past_finals():
    sched = pd.DataFrame([
        {"week": 1, "game_type": "REG", "home_score": 24, "away_score": 17},
        {"week": 2, "game_type": "REG", "home_score": None, "away_score": None},
        {"week": 19, "game_type": "POST", "home_score": None, "away_score": None},
    ])
    assert _next_unplayed_reg_week(sched) == 2


def test_next_unplayed_reg_week_falls_back_to_last_when_final():
    sched = pd.DataFrame([
        {"week": 1, "game_type": "REG", "home_score": 24, "away_score": 17},
        {"week": 18, "game_type": "REG", "home_score": 31, "away_score": 14},
        {"week": 19, "game_type": "POST", "home_score": None, "away_score": None},
    ])
    assert _next_unplayed_reg_week(sched) == 18


def test_reg_weeks_from_sched_ignores_preseason():
    sched = pd.DataFrame([
        {"week": 1, "game_type": "PRE"},
        {"week": 1, "game_type": "REG"},
        {"week": 1, "game_type": "REG"},
        {"week": 2, "game_type": "REG"},
    ])
    assert _reg_weeks_from_sched(sched) == [
        {"week": 1, "games": 2},
        {"week": 2, "games": 1},
    ]
