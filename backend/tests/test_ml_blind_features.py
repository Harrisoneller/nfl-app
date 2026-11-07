"""Market allowlist and the week-1 EPA cutoff. No network and no holdout."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from app.services.ml_blind_features import (
    build_market_blind_frame,
    model_matrix,
    team_game_epa,
)
from app.services.ml_blind_spec import (
    ATS_NONINFERIORITY,
    BOOTSTRAP_PERCENTILES,
    BOOTSTRAP_RESAMPLES,
    ELO_HOME_FIELD,
    ELO_INITIAL,
    ELO_K,
    ELO_SEASON_CARRY,
    EPA_HALF_LIFE_GAMES,
    EPA_WINDOW,
    FEATURE_COLUMNS,
    INCUMBENT_COMMIT,
    INCUMBENT_DEPLOYMENT,
    INCUMBENT_MODEL_VERSION,
    PLATT_EPOCHS,
    PLATT_LEARNING_RATE,
    PLATT_MIN_ROWS,
    PREREG_SHA,
    RESULT_LAG_DAYS,
    SEED,
    SIGMA,
    SPREAD_MODEL_PARAMS,
    TEST_SEASON_FIRST,
    TEST_SEASON_LAST,
    TRAIN_FIRST_SEASON,
    WIN_MODEL_PARAMS,
    assert_feature_allowlist,
    assert_no_market_columns,
)
from app.services.ml_blind_spec import (
    test_seasons as locked_seasons,
)
from app.services.ml_predictions_service import fit_market_blind_models

_SPEC = Path(__file__).resolve().parents[2] / "research" / "nfl-ml-blind" / "TRAIN_SPEC.json"
_MARKET_NAMES = (
    "spread_line",
    "total_line",
    "home_moneyline",
    "away_moneyline",
    "market_home_win_prob",
    "market_baseline_home_margin",
    "vegas_wp",
    "vegas_home_wp",
    "home_spread_odds",
    "over_odds",
    "implied_prob",
    "closing_spread",
)


def _game(game_id, gameday, home, away, **extra):
    row = {
        "game_id": game_id,
        "season": extra.pop("season", 2024),
        "week": extra.pop("week", 1),
        "gameday": gameday,
        "game_type": extra.pop("game_type", "REG"),
        "home_team": home,
        "away_team": away,
        "home_score": extra.pop("home_score", 20),
        "away_score": extra.pop("away_score", 17),
        "location": extra.pop("location", "Home"),
        "roof": extra.pop("roof", "outdoors"),
    }
    row.update(extra)
    return row


def _epa(game_id, gameday, team, off, deff=0.0):
    return {
        "game_id": game_id,
        "gameday": gameday,
        "team": team,
        "off_epa": off,
        "def_epa": deff,
    }


def _row(frame, game_id):
    return frame.loc[frame["game_id"] == game_id].iloc[0]


def test_allowlist_rejects_market_names_and_unknown_columns():
    assert_feature_allowlist(FEATURE_COLUMNS)
    for name in _MARKET_NAMES:
        with pytest.raises(ValueError, match="market columns"):
            assert_feature_allowlist([*FEATURE_COLUMNS[:2], name])
        with pytest.raises(ValueError, match="market columns"):
            assert_no_market_columns(["home_elo", name])
    with pytest.raises(ValueError, match="allowlist"):
        assert_feature_allowlist(["home_elo", "made_up_feature"])


def test_train_spec_matches_the_prereg_constants():
    spec = json.loads(_SPEC.read_text())
    assert spec["prereg_sha"] == PREREG_SHA
    assert spec["seed"] == SEED == 20261026
    assert spec["sigma"] == SIGMA == 13.5
    assert spec["result_lag_days"] == RESULT_LAG_DAYS == 2
    assert spec["train_first_season"] == TRAIN_FIRST_SEASON == 2012
    assert spec["test_seasons"] == locked_seasons()
    assert spec["test_seasons"][0] == TEST_SEASON_FIRST == 2016
    assert spec["test_seasons"][-1] == TEST_SEASON_LAST == 2025
    assert spec["bootstrap_resamples"] == BOOTSTRAP_RESAMPLES == 10_000
    assert spec["bootstrap_percentiles"] == list(BOOTSTRAP_PERCENTILES)
    assert spec["ats_noninferiority"] == ATS_NONINFERIORITY == -0.005
    assert spec["epa_window"] == EPA_WINDOW == 4
    assert spec["epa_half_life_games"] == EPA_HALF_LIFE_GAMES == 3.5
    assert spec["features"] == list(FEATURE_COLUMNS)
    assert spec["spread_model"] == SPREAD_MODEL_PARAMS
    assert spec["win_model"] == WIN_MODEL_PARAMS
    assert spec["platt"] == {
        "epochs": PLATT_EPOCHS,
        "learning_rate": PLATT_LEARNING_RATE,
        "min_rows": PLATT_MIN_ROWS,
    }
    assert spec["elo"] == {
        "k": ELO_K,
        "home_field": ELO_HOME_FIELD,
        "initial": ELO_INITIAL,
        "season_carry": ELO_SEASON_CARRY,
    }
    assert spec["incumbent"]["model_version"] == INCUMBENT_MODEL_VERSION
    assert spec["incumbent"]["commit"] == INCUMBENT_COMMIT
    assert spec["incumbent"]["deployment"] == INCUMBENT_DEPLOYMENT


def test_week1_fallback_ignores_same_week_and_future_epa():
    """Week 1 cannot read itself, the rest of that week, or the next day."""
    schedule = pd.DataFrame([
        _game("w1", "2024-09-08", "KC", "DEN", week=1, home_score=24, away_score=17),
        _game("sun", "2024-09-08", "BUF", "MIA", week=1),
        _game("next", "2024-09-09", "PHI", "ATL", week=1),
        _game("later", "2024-09-15", "KC", "LV", week=2, home_score=21, away_score=14),
    ])
    leaked = pd.DataFrame([
        _epa("w1", "2024-09-08", "KC", 5.0),
        _epa("w1", "2024-09-08", "DEN", 4.0),
        _epa("sun", "2024-09-08", "BUF", 9.0),
        _epa("next", "2024-09-09", "PHI", 8.0),
        _epa("later", "2024-09-15", "KC", -3.0),
    ])
    blank = pd.DataFrame(columns=["game_id", "gameday", "team", "off_epa", "def_epa"])
    with_leak = build_market_blind_frame(schedule, leaked)
    with_none = build_market_blind_frame(schedule, blank)
    for game_id in ("w1", "sun", "next"):
        assert model_matrix(with_leak[with_leak["game_id"] == game_id]).reset_index(drop=True).equals(
            model_matrix(with_none[with_none["game_id"] == game_id]).reset_index(drop=True)
        )
        assert _row(with_leak, game_id)["home_off_epa"] == 0.0
        assert _row(with_leak, game_id)["away_off_epa"] == 0.0


def test_early_season_fallback_uses_only_cutoff_eligible_history():
    schedule = pd.DataFrame([
        _game("prior", "2024-01-28", "KC", "DEN", season=2023, week=21, game_type="POST",
              home_score=25, away_score=22),
        _game("day_before", "2024-09-07", "KC", "LV", week=1, home_score=10, away_score=7),
        _game("w1", "2024-09-08", "KC", "DEN", week=1, home_score=20, away_score=17),
        _game("future", "2024-09-22", "KC", "BUF", week=3),
    ])
    base = pd.DataFrame([
        _epa("prior", "2024-01-28", "KC", 2.0, 0.1),
        _epa("day_before", "2024-09-07", "KC", 9.0, 1.5),
        _epa("w1", "2024-09-08", "KC", 5.0, 0.4),
        _epa("future", "2024-09-22", "KC", -4.0, 2.0),
    ])
    scrambled = base.copy()
    scrambled.loc[scrambled["game_id"].isin(["day_before", "w1", "future"]), "off_epa"] = 50.0
    scrambled.loc[scrambled["game_id"].isin(["day_before", "w1", "future"]), "def_epa"] = -7.0
    original = _row(build_market_blind_frame(schedule, base), "w1")
    mutated = _row(build_market_blind_frame(schedule, scrambled), "w1")
    assert original["home_off_epa"] == pytest.approx(2.0)
    assert mutated["home_off_epa"] == pytest.approx(2.0)
    assert mutated["home_def_epa"] == pytest.approx(0.1)
    # The day-before game is on the schedule, so rest can see the date, not the EPA.
    assert original["rest_days_home"] == pytest.approx(3.0)
    assert original["short_week_home"] == 1


def test_cutoff_date_is_included_and_the_next_day_is_not():
    schedule = pd.DataFrame([
        _game("old", "2024-09-05", "KC", "DEN", home_score=27, away_score=10),
        _game("edge", "2024-09-06", "KC", "LV", home_score=14, away_score=13),
        _game("w1", "2024-09-08", "KC", "BUF"),
    ])
    games = pd.DataFrame([
        _epa("old", "2024-09-05", "KC", 1.0),
        _epa("edge", "2024-09-06", "KC", 3.0),
    ])
    row = _row(build_market_blind_frame(schedule, games), "w1")
    assert row["home_off_epa"] == pytest.approx(2.0)


def test_market_columns_on_the_schedule_do_not_change_features():
    clean = pd.DataFrame([
        _game("g", "2024-09-08", "KC", "PHI", roof="dome"),
    ])
    dirty = clean.copy()
    dirty["spread_line"] = -6.5
    dirty["total_line"] = 47.5
    dirty["home_moneyline"] = -280
    dirty["away_moneyline"] = 230
    dirty["wind"] = 40
    dirty["temp"] = 10
    left = build_market_blind_frame(clean)
    right = build_market_blind_frame(dirty)
    assert "spread_line" not in right.columns
    assert "home_moneyline" not in right.columns
    assert model_matrix(left).equals(model_matrix(right))
    assert _row(left, "g")["weather_class_code"] == 0
    assert _row(left, "g")["weather_is_bad"] == 0
    assert _row(left, "g")["is_division_game"] == 0
    assert _row(left, "g")["away_travel_proxy"] == pytest.approx(1.0)


def test_elo_does_not_ingest_a_result_inside_the_lag():
    early = pd.DataFrame([
        _game("blowout", "2024-09-01", "KC", "DEN", home_score=35, away_score=0),
        _game("w1", "2024-09-08", "KC", "LV", home_score=21, away_score=17),
    ])
    recent = pd.concat([
        early,
        pd.DataFrame([
            _game("yesterday", "2024-09-07", "KC", "BUF", home_score=0, away_score=40),
        ]),
    ], ignore_index=True)
    kept = _row(build_market_blind_frame(early), "w1")["home_elo"]
    leaked = _row(build_market_blind_frame(recent), "w1")["home_elo"]
    assert kept > 1500
    assert leaked == pytest.approx(kept)


def test_pbp_week1_plays_do_not_enter_that_games_features():
    schedule = pd.DataFrame([
        _game("w1", "2024-09-08", "KC", "DEN"),
        _game("prior", "2024-01-14", "KC", "BUF", season=2023, week=19, game_type="POST"),
    ])
    plays = pd.DataFrame([
        {"game_id": "prior", "posteam": "KC", "defteam": "BUF", "epa": 2.0, "play_type": "pass",
         "season_type": "POST"},
        {"game_id": "w1", "posteam": "KC", "defteam": "DEN", "epa": 9.0, "play_type": "pass",
         "season_type": "REG"},
        {"game_id": "w1", "posteam": "KC", "defteam": "DEN", "epa": -9.0, "play_type": "pass",
         "season_type": "REG"},
    ])
    frame = build_market_blind_frame(schedule, team_game_epa(plays, schedule))
    assert _row(frame, "w1")["home_off_epa"] == pytest.approx(2.0)


def test_fit_targets_raw_margin_and_rejects_a_market_column():
    rows = []
    for i in range(40):
        row = {col: 0.0 for col in FEATURE_COLUMNS}
        row["season"] = 2020
        row["week_number"] = (i % 17) + 1
        row["game_type"] = "REG"
        row["home_margin"] = 4.0 if i % 2 == 0 else -2.0
        rows.append(row)
    frame = pd.DataFrame(rows)
    fitted = fit_market_blind_models(frame)
    assert fitted["trained"] is True
    assert list(fitted["spread_model"].feature_names_in_) == list(FEATURE_COLUMNS)
    preds = fitted["spread_model"].predict(model_matrix(frame))
    assert float(preds.mean()) == pytest.approx(1.0, abs=0.15)
    dirty = frame.copy()
    dirty["spread_line"] = -3.0
    with pytest.raises(ValueError, match="market columns"):
        fit_market_blind_models(dirty)
