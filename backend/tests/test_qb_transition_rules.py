"""Point-in-time transition rules. No holdout numbers."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "qb_transition_ablation",
    ROOT / "backend" / "scripts" / "qb_transition_ablation.py",
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def test_only_out_and_doubtful_trigger_a_change():
    assert mod.report_triggers("Out")
    assert mod.report_triggers("doubtful")
    assert not mod.report_triggers("Questionable")
    assert not mod.report_triggers(None)
    assert not mod.report_triggers("Note")


def test_surprise_starter_change_is_unknowable_pre_2025():
    assert mod.unknowable_pre2025("QB2", "QB1", report_triggered=False)
    assert not mod.unknowable_pre2025("QB2", "QB1", report_triggered=True)
    assert not mod.unknowable_pre2025("QB1", "QB1", report_triggered=False)
    assert not mod.unknowable_pre2025("QB2", None, report_triggered=False)


def test_dated_depth_miss_is_unknowable():
    assert mod.unknowable_dated("QB2", "QB1", "QB1")
    assert not mod.unknowable_dated("QB2", "QB1", "QB2")
    assert mod.unknowable_dated("QB2", "QB1", None)
    assert not mod.unknowable_dated("QB1", "QB1", None)


def test_slope_has_no_intercept():
    x = np.array([0.0, 0.0, 1.0, 2.0])
    y = np.array([5.0, 5.0, 3.0, 6.0])
    # Through the origin, the two zeros do not pull in an intercept.
    assert mod.ols_through_origin(y, x) == np.dot(x, y) / np.dot(x, x)


def test_replacement_strips_only_the_transitioning_side():
    margin = np.array([3.0, 3.0, 3.0])
    feature = np.array([0.0, 0.4, -0.1])
    old_dh = np.array([0.1, 0.2, 0.2])
    old_da = np.array([0.05, 0.05, 0.3])
    home_t = np.array([0.0, 1.0, 0.0])
    away_t = np.array([0.0, 0.0, 1.0])
    pred = mod.replacement_margin(margin, feature, old_dh, old_da, home_t, away_t, 10.0, 4.0)
    assert pred[0] == 3.0
    assert pred[1] == 3.0 - 10.0 * 0.2 + 4.0 * 0.4
    assert pred[2] == 3.0 - 10.0 * (0.0 - 0.3) + 4.0 * -0.1
    total = mod.replacement_total(
        np.array([45.0, 45.0]), np.array([0.0, 0.4]),
        np.array([0.2, 0.2]), np.array([0.1, 0.1]),
        np.array([0.0, 1.0]), np.array([0.0, 0.0]), 12.0, 5.0,
    )
    assert total[0] == 45.0
    assert total[1] == 45.0 - 12.0 * 0.2 + 5.0 * 0.4


def test_walk_forward_ignores_2015_and_uses_zero_weight_in_2016():
    rows = []
    for i in range(40):
        rows.append({
            "season": 2015, "unknowable": 0, "result": 50.0, "margin": 0.0,
            "spread_line": 1.0, "f_margin": 1.0, "old_dh": 0.0, "old_da": 0.0,
            "home_transition": 1, "away_transition": 0, "pass_coef": 10.0,
        })
    rows.append({
        "season": 2016, "unknowable": 0, "result": 0.0, "margin": 5.0,
        "spread_line": 1.0, "f_margin": 0.5, "old_dh": 0.2, "old_da": 0.0,
        "home_transition": 1, "away_transition": 0, "pass_coef": 10.0,
    })
    frame = __import__("pandas").DataFrame(rows)
    _crps, by_season = mod.walk_forward_crps(frame)
    assert [row["season"] for row in by_season] == [2016]
    assert by_season[0]["w_margin"] == 0.0
    mu = np.array([5.0 - 10.0 * 0.2])
    expected = float(mod.v6.crps_normal(np.array([0.0]), mu, mod.v6.MARGIN_SIGMA).mean())
    assert by_season[0]["crps"] == expected


def test_bar_fails_closed_when_the_rate_misses():
    summary = {
        "ats": 0.51,
        "n_ats": 500,
        "bootstrap_90_lo": 0.49,
        "bootstrap_90_hi": 0.53,
        "margin_crps": 7.2,
        "v6_margin_crps": 7.1,
        "p_value": 0.2,
    }
    verdict = mod.bar_verdict(summary)
    assert verdict["overall_pass"] is False
    assert verdict["ats_52_5_on_400"]["pass"] is False
    assert verdict["clv"]["status"] == "unavailable"
    assert verdict["no_crps_regression_vs_v6"]["pass"] is False
