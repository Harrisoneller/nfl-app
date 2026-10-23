"""Rules for the wind-on-totals ablation. No holdout grades."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "wind_totals_ablation",
    ROOT / "backend" / "scripts" / "wind_totals_ablation.py",
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def test_schedule_columns_exclude_observed_weather():
    joined = mod.FETCH_COLUMNS + mod.GRADE_COLUMNS
    assert "wind" not in joined
    assert "temp" not in joined
    assert not any(str(column).startswith("weather") for column in joined)


def test_dome_and_closed_roof_are_zero_even_in_a_high_forecast():
    assert mod.applied_wind("dome", "Home", 28.0, is_indoor=True) == 0.0
    assert mod.applied_wind("closed", "Home", 28.0, is_indoor=True) == 0.0
    assert mod.applied_wind("closed", "Home", 28.0, is_indoor=False) == 0.0


def test_outdoors_and_open_roof_keep_the_forecast():
    assert mod.applied_wind("outdoors", "Home", 18.0, is_indoor=False) == 18.0
    # Retractable roof open: the indoor flag does not zero the forecast.
    assert mod.applied_wind("open", "Home", 16.0, is_indoor=True) == 16.0


def test_neutral_site_is_zero():
    assert mod.applied_wind("outdoors", "Neutral", 20.0, is_indoor=False) == 0.0
    assert not mod.uses_forecast("outdoors", "Neutral", False)


def test_missing_roof_uses_the_indoor_flag_only_as_fallback():
    assert mod.applied_wind(None, "Home", 14.0, is_indoor=True) == 0.0
    assert mod.applied_wind(None, "Home", 14.0, is_indoor=False) == 14.0


def test_missing_forecast_does_not_move_the_total():
    assert mod.applied_wind("outdoors", "Home", None, is_indoor=False) == 0.0
    assert mod.predict_total([45.0, 45.0], [0.0, 0.0], -0.22).tolist() == [45.0, 45.0]
    moved = mod.predict_total([45.0], [3.0], -0.22)
    assert moved[0] == pytest.approx(45.0 - 0.66)


def test_hinge_and_step_and_cap():
    assert mod.wind_feature(12.0, "hinge", 12.0) == 0.0
    assert mod.wind_feature(15.0, "hinge", 12.0) == 3.0
    assert mod.wind_feature(40.0, "hinge", 12.0) == 18.0
    assert mod.wind_feature(12.0, "step", 12.0) == 1.0
    assert mod.wind_feature(11.9, "step", 12.0) == 0.0


def test_ols_ignores_zero_feature_rows():
    x = np.array([0.0, 0.0, 2.0, 4.0])
    y = np.array([9.0, -3.0, 1.0, 2.0])
    assert mod.ols_through_origin(y, x) == pytest.approx(np.dot(x, y) / np.dot(x, x))


def test_issue_time_is_the_kickoff_hour_minus_24h():
    kick = mod.kickoff_utc("2024-09-05", "20:20")
    assert kick == pd.Timestamp("2024-09-06 00:20", tz="UTC")
    index = pd.date_range("2024-09-05", periods=48, freq="h", tz="UTC")
    series = pd.Series(np.linspace(1, 5, len(index)), index=index)
    chosen = mod.choose_forecast(series, kick)
    assert chosen["valid_time_utc"] == pd.Timestamp("2024-09-06 00:00", tz="UTC")
    assert chosen["issue_time_utc"] == pd.Timestamp("2024-09-05 00:00", tz="UTC")
    assert chosen["issue_time_utc"] <= kick - pd.Timedelta(hours=24)


def test_null_kickoff_hour_walks_backward_and_skips_later_hours():
    kick = pd.Timestamp("2024-09-06 00:20", tz="UTC")
    index = pd.to_datetime(
        ["2024-09-05T23:00", "2024-09-06T00:00", "2024-09-06T01:00"], utc=True,
    )
    series = pd.Series([8.0, np.nan, 30.0], index=index)
    chosen = mod.choose_forecast(series, kick)
    assert chosen["wind_mph"] == 8.0
    assert chosen["valid_time_utc"] == pd.Timestamp("2024-09-05 23:00", tz="UTC")
    assert chosen["issue_time_utc"] == pd.Timestamp("2024-09-04 23:00", tz="UTC")


def test_tie_break_prefers_threshold_12_then_hinge():
    candidates = [
        {"form": "hinge", "threshold": 10.0, "walk_forward_totals_crps": 1.0},
        {"form": "step", "threshold": 12.0, "walk_forward_totals_crps": 1.0 + 5e-7},
        {"form": "hinge", "threshold": 12.0, "walk_forward_totals_crps": 1.0 + 5e-7},
    ]
    chosen = mod.choose_candidate(candidates)
    assert chosen["form"] == "hinge"
    assert chosen["threshold"] == 12.0
    clear = [
        {"form": "hinge", "threshold": 10.0, "walk_forward_totals_crps": 1.0},
        {"form": "hinge", "threshold": 12.0, "walk_forward_totals_crps": 1.01},
    ]
    assert mod.choose_candidate(clear)["threshold"] == 10.0


def test_walk_forward_rejects_the_holdout_and_zeros_2016():
    later = pd.DataFrame({
        "season": [2024] * 5,
        "actual_total": [40.0] * 5,
        "v6_total": [45.0] * 5,
        "total_line": [44.0] * 5,
        "feature": [1.0] * 5,
    })
    with pytest.raises(RuntimeError):
        mod.walk_forward_totals_crps(later)
    first = pd.DataFrame({
        "season": [2016, 2016],
        "actual_total": [40.0, 50.0],
        "v6_total": [45.0, 45.0],
        "total_line": [44.0, 44.0],
        "feature": [5.0, 5.0],
    })
    _crps, rows = mod.walk_forward_totals_crps(first)
    assert rows[0]["w"] == 0.0


def test_bar_fails_closed_and_clv_is_not_a_pass():
    passing = {
        "ats": 0.60, "n_ats": 500, "wins": 300,
        "bootstrap_90_lo": 0.55, "bootstrap_90_hi": 0.65,
        "totals_crps": 7.0, "v6_totals_crps": 7.1, "close_totals_crps": 6.8,
        "p_value": 0.001,
    }
    good = mod.bar_verdict(passing)
    assert good["overall_pass"]
    assert good["clv"]["pass"] is None
    assert good["clv"]["status"] == "unavailable"

    short = dict(passing, ats=0.50, wins=250)
    assert not mod.bar_verdict(short)["overall_pass"]
    thin = dict(passing, n_ats=399, wins=240)
    assert not mod.bar_verdict(thin)["overall_pass"]
    wide = dict(passing, bootstrap_90_lo=0.50)
    assert not mod.bar_verdict(wide)["overall_pass"]
    worse = dict(passing, totals_crps=7.2)
    assert not mod.bar_verdict(worse)["overall_pass"]
    weak = dict(passing, p_value=0.01)
    assert not mod.bar_verdict(weak)["overall_pass"]


def test_selection_payload_rejects_a_holdout_grade():
    ok = {"holdout_scored": False, "scored_seasons_max": 2023, "candidates": [], "windy_subset_2024_25_n": 10}
    mod.assert_selection_payload(ok)
    bad = dict(ok, ats=0.5)
    with pytest.raises(RuntimeError):
        mod.assert_selection_payload(bad)
