"""Gate arithmetic on synthetic rows. No NFL fit and no holdout score."""
from __future__ import annotations

import pandas as pd
import pytest

from app.services.ml_blind_incumbent import refit_stage2_margin, rows_before_season
from app.services.ml_blind_protocol import (
    bootstrap_mean_ci,
    cluster_se_of_mean,
    grade_predictions,
    inner_oof_plan,
    projected_power,
    selection_opens_final,
    selection_z,
    verdict,
)
from app.services.ml_blind_spec import MDE_CRPS, SEED, Z_CRIT_95, Z_MIN, ats_cluster_lower_rate


def _row(**overrides):
    base = {
        "actual_margin": 10.0,
        "candidate_margin": 10.0,
        "incumbent_margin": 0.0,
        "candidate_prob": 0.7,
        "incumbent_prob": 0.5,
        "close": 3.0,
        "cluster": "2024-1",
    }
    base.update(overrides)
    return base


def test_reported_incumbent_crps_gain_is_descriptive():
    summary = grade_predictions([
        _row(cluster="2024-1"),
        _row(actual_margin=4.0, candidate_margin=4.0, cluster="2024-2"),
    ])
    assert summary["crps_gain_vs_incumbent"]["mean"] > 0
    assert summary["brier_gain"]["mean"] > 0
    assert summary["logloss_gain"]["mean"] > 0
    assert summary["enables_ml"] is False
    assert summary["merge_authorized"] is False
    assert summary["close_ni"]["ci"]["hi"] <= 0.25
    assert summary["verdict"] == "SHIP"


def test_ats_difference_uses_home_margin_sign_and_drops_pushes():
    rows = [
        _row(candidate_margin=10.0, incumbent_margin=0.0, close=3.0, actual_margin=14.0, cluster="2024-1"),
        _row(candidate_margin=0.0, incumbent_margin=10.0, close=3.0, actual_margin=14.0, cluster="2024-2"),
        _row(candidate_margin=10.0, incumbent_margin=10.0, close=3.0, actual_margin=3.0, cluster="2024-3"),
        _row(candidate_margin=3.0, incumbent_margin=10.0, close=3.0, actual_margin=14.0, cluster="2024-4"),
    ]
    summary = grade_predictions(rows)
    assert summary["ats"]["n"] == 2
    assert summary["ats"]["diff"] == pytest.approx(0.0)
    assert summary["ats"]["candidate_rate"] == pytest.approx(0.5)
    assert summary["ats"]["incumbent_rate"] == pytest.approx(0.5)
    assert summary["ats"]["game_bootstrap"]["role"] == "descriptive_game_bootstrap"
    assert summary["ats"]["cluster_ci"]["role"] == "gate_cluster_bootstrap"


def test_close_ni_and_ats_bounds_are_the_only_ship_gates():
    floor = ats_cluster_lower_rate()
    passing = {
        "close_ni": {"ci": {"lo": -0.2, "hi": 0.25}},
        "ats": {"cluster_ci": {"lo": floor, "hi": 0.0}},
    }
    assert verdict(passing) == "SHIP"
    assert verdict({"close_ni": {"ci": {"hi": 0.25 + 1e-9}}, "ats": passing["ats"]}) == "NO-SHIP"
    assert verdict({
        "close_ni": passing["close_ni"],
        "ats": {"cluster_ci": {"lo": floor - 1e-9, "hi": 0.0}},
    }) == "NO-SHIP"


def test_bootstrap_interval_is_paired_and_seeded():
    samples = [1.0, -1.0, 0.5, 0.0]
    first = bootstrap_mean_ci(samples, seed=SEED, resamples=200)
    second = bootstrap_mean_ci(samples, seed=SEED, resamples=200)
    other = bootstrap_mean_ci(samples, seed=SEED + 1, resamples=200)
    assert first == second
    assert other["lo"] != first["lo"] or other["hi"] != first["hi"]


def test_cluster_se_matches_the_two_cluster_formula():
    values = [0.0, 0.0, 2.0, 2.0]
    clusters = ["a", "a", "b", "b"]
    assert cluster_se_of_mean(values, clusters) == pytest.approx(1.0)


def test_selection_requires_both_floors_and_does_not_open_on_a_miss():
    se_at_half_power = MDE_CRPS / Z_CRIT_95
    assert projected_power(se_at_half_power) == pytest.approx(0.5, abs=1e-6)
    opened = selection_opens_final(-1.0, 0.01, se_at_half_power * 0.99)
    assert opened["z"] >= Z_MIN
    assert opened["power"] > 0.5
    assert opened["open_final"] is True
    assert selection_z(0.25, 0.1) == pytest.approx(0.0)
    assert selection_opens_final(0.2, 0.1, se_at_half_power)["open_final"] is False
    assert projected_power(se_at_half_power * 1.01) < 0.5
    assert selection_opens_final(-1.0, 0.01, se_at_half_power * 1.01)["open_final"] is False


def test_inner_oof_plan_keeps_each_season_out_of_its_own_fold():
    plan = inner_oof_plan([2015, 2012, 2014, 2013])
    assert plan[0] == (2013, (2012,))
    assert plan[-1] == (2015, (2012, 2013, 2014))
    assert all(cal not in prior for cal, prior in plan)
    assert 2016 not in [cal for cal, _prior in plan]


def test_stage2_refit_drops_the_target_season():
    rows = []
    for i in range(8):
        row = {name: 0.0 for name in ("mkt_d", "epa_d", "pts_d", "st_d", "qb_d", "hfa")}
        row["epa_d"] = float(i)
        row["hfa"] = 1.0
        row["result"] = 2.0 * float(i)
        row["season"] = 2014
        rows.append(row)
    prior = pd.DataFrame(rows)
    future = prior.iloc[[0]].copy()
    future["season"] = 2016
    future["result"] = 500.0
    both = pd.concat([prior, future], ignore_index=True)
    assert len(rows_before_season(both, 2016)) == 8
    assert refit_stage2_margin(both, 2016) == pytest.approx(refit_stage2_margin(prior, 2016))
