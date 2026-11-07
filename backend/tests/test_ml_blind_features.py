"""Market allowlist and the week-1 EPA cutoff. No network and no holdout."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from app.services.ml_blind_features import (
    _update_elo,
    assert_prefit_coverage,
    build_market_blind_frame,
    model_matrix,
    team_game_epa,
)
from app.services.ml_blind_guard import LockRefusal
from app.services.ml_blind_spec import (
    FEATURE_COLUMNS,
    assert_feature_allowlist,
    assert_no_market_columns,
    spec_document,
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
    assert spec == spec_document()
    assert "prereg_sha" not in spec
    assert spec["close_ni_upper"] == 0.25
    assert spec["ats_cluster_lower_pp"] == -2.0
    assert spec["z_min"] == 2.0
    assert spec["power_min"] == 0.5
    assert spec["selection_seasons"] == [2013, 2014, 2015]
    assert spec["final_seasons"][0] == 2016
    assert spec["final_seasons"][-1] == 2025
    assert spec["incumbent"] == "walk_forward_stage2"
    assert spec["platt"] == "inner_season_oof"
    assert spec["ship_enables_ml"] is False
    assert spec["ship_authorizes_merge"] is False


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


def test_fit_refuses_before_any_tree_is_built():
    with pytest.raises(LockRefusal, match="SHA"):
        fit_market_blind_models(pd.DataFrame())


def test_season_opener_elo_is_regressed_before_the_row_is_read():
    schedule = pd.DataFrame([
        _game("prior", "2023-12-31", "KC", "DEN", season=2023, week=17,
              home_score=35, away_score=0),
        _game("opener", "2024-09-08", "KC", "LV", season=2024, week=1,
              home_score=20, away_score=17),
    ])
    frame = build_market_blind_frame(schedule)
    raw_home, _raw_away = _update_elo(1500.0, 1500.0, 35, False)
    carried = 0.75 * raw_home + 0.25 * 1500.0
    opener = _row(frame, "opener")
    assert opener["home_elo"] == pytest.approx(carried)
    assert opener["home_elo"] != pytest.approx(raw_home)
    assert opener["home_elo"] < raw_home


def test_monday_is_eligible_for_thursday_and_the_day_before_is_not():
    """D-3 is inside d <= D-2. D-1 is not. Same-week is not a blanket exclusion."""
    schedule = pd.DataFrame([
        _game("mon", "2024-09-09", "KC", "DEN", week=1, home_score=28, away_score=7),
        _game("wed", "2024-09-11", "KC", "LV", week=1, home_score=3, away_score=30),
        _game("thu", "2024-09-12", "KC", "BUF", week=1, home_score=21, away_score=17),
    ])
    base = pd.DataFrame([
        _epa("mon", "2024-09-09", "KC", 2.0),
        _epa("wed", "2024-09-11", "KC", 9.0),
    ])
    scrambled = base.copy()
    scrambled.loc[scrambled["game_id"] == "wed", "off_epa"] = 40.0
    original = _row(build_market_blind_frame(schedule, base), "thu")
    mutated = _row(build_market_blind_frame(schedule, scrambled), "thu")
    assert original["home_off_epa"] == pytest.approx(2.0)
    assert mutated["home_off_epa"] == pytest.approx(2.0)


def _slate(game_id, gameday, season, week, game_type, home, away):
    return _game(
        game_id, gameday, home, away, season=season, week=week, game_type=game_type,
        home_score=20, away_score=10,
    )


def test_prefit_coverage_accepts_2011_history_and_rejects_gaps():
    schedule = pd.DataFrame([
        _slate("h", "2011-09-11", 2011, 1, "REG", "KC", "DEN"),
        _slate("y", "2012-09-09", 2012, 1, "REG", "KC", "DEN"),
    ])
    history = pd.DataFrame([
        _epa("h", "2011-09-11", "KC", 0.1),
        _epa("h", "2011-09-11", "DEN", -0.1),
    ])
    history["season"] = 2011
    assert assert_prefit_coverage(schedule, history) == {
        "reg_team_games": 2,
        "missing_epa_history": 0,
        "unknown_ids": 0,
        "has_2011": 1,
    }
    missing_den = history[history["team"] == "KC"]
    with pytest.raises(ValueError, match="no eligible EPA"):
        assert_prefit_coverage(schedule, missing_den)
    unknown = schedule.copy()
    unknown.loc[unknown["game_id"] == "y", "away_team"] = "XXX"
    with pytest.raises(ValueError, match="unknown team ids"):
        assert_prefit_coverage(unknown, history)
    no_prior = schedule[schedule["season"] == 2012]
    later = pd.DataFrame([
        _epa("early", "2012-09-01", "KC", 0.2),
        _epa("early", "2012-09-01", "DEN", 0.2),
    ])
    later["season"] = 2012
    with pytest.raises(ValueError, match="2011"):
        assert_prefit_coverage(no_prior, later)


def _plays(game_id, season, offense, defense, epa):
    return {
        "game_id": game_id,
        "season": season,
        "season_type": "REG",
        "play_type": "pass",
        "posteam": offense,
        "defteam": defense,
        "epa": epa,
    }


def test_prefit_coverage_reads_season_from_the_real_epa_builder():
    """The coverage check must see season on team_game_epa output, not a hand-built column."""
    schedule = pd.DataFrame([
        _slate("h", "2011-09-11", 2011, 1, "REG", "KC", "DEN"),
        _slate("y", "2012-09-09", 2012, 1, "REG", "KC", "DEN"),
    ])
    plays = pd.DataFrame([
        _plays("h", 2011, "KC", "DEN", 0.2),
        _plays("h", 2011, "DEN", "KC", -0.1),
        _plays("y", 2012, "KC", "DEN", 0.3),
        _plays("y", 2012, "DEN", "KC", 0.0),
    ])
    built = team_game_epa(plays, schedule)
    assert "season" in built.columns
    assert set(built["season"].astype(int)) == {2011, 2012}
    counts = assert_prefit_coverage(schedule, built)
    assert counts == {
        "reg_team_games": 2,
        "missing_epa_history": 0,
        "unknown_ids": 0,
        "has_2011": 1,
    }
    only_2012 = team_game_epa(plays[plays["season"] == 2012], schedule[schedule["season"] == 2012])
    assert "season" in only_2012.columns
    with pytest.raises(ValueError, match="2011"):
        assert_prefit_coverage(schedule[schedule["season"] == 2012], only_2012)
