"""Locked scoring rules. These tests do not fit on NFL games."""
from __future__ import annotations

import pytest

from app.services.ml_blind_protocol import (
    bootstrap_mean_ci,
    grade_predictions,
    require_qa_lock,
    verdict,
)
from app.services.ml_blind_spec import ATS_NONINFERIORITY, SEED


def _row(**overrides):
    base = {
        "actual_margin": 10.0,
        "candidate_margin": 10.0,
        "incumbent_margin": 0.0,
        "candidate_prob": 0.7,
        "incumbent_prob": 0.5,
        "close": 3.0,
    }
    base.update(overrides)
    return base


def test_eval_refuses_without_the_qa_lock(monkeypatch):
    monkeypatch.delenv("NFL_ML_BLIND_EVAL_CONFIRM", raising=False)
    with pytest.raises(SystemExit, match="QA-LOCK"):
        require_qa_lock()


def test_crps_gain_is_positive_when_the_candidate_is_closer():
    summary = grade_predictions([_row(), _row(actual_margin=4.0, candidate_margin=4.0)])
    assert summary["crps_gain"]["mean"] > 0
    assert summary["brier_gain"]["mean"] > 0
    assert summary["logloss_gain"]["mean"] > 0
    assert summary["crps_beats"] is True or summary["crps_gain"]["lo"] <= 0


def test_ats_difference_uses_home_margin_sign_and_drops_pushes():
    rows = [
        _row(candidate_margin=10.0, incumbent_margin=0.0, close=3.0, actual_margin=14.0),
        _row(candidate_margin=0.0, incumbent_margin=10.0, close=3.0, actual_margin=14.0),
        _row(candidate_margin=10.0, incumbent_margin=10.0, close=3.0, actual_margin=3.0),
        _row(candidate_margin=3.0, incumbent_margin=10.0, close=3.0, actual_margin=14.0),
    ]
    summary = grade_predictions(rows)
    # Push and the candidate's exact-close no-bet are out. Two bets remain: +1 and -1.
    assert summary["ats"]["n"] == 2
    assert summary["ats"]["diff"] == pytest.approx(0.0)
    assert summary["ats"]["candidate_rate"] == pytest.approx(0.5)
    assert summary["ats"]["incumbent_rate"] == pytest.approx(0.5)


def test_close_noninferiority_boundary_is_half_a_percentage_point():
    passing = {
        "crps_gain": {"mean": 0.0, "lo": -0.1},
        "brier_gain": {"mean": 0.0, "lo": -0.1},
        "logloss_gain": {"mean": 0.0, "lo": -0.1},
        "ats": {"diff": ATS_NONINFERIORITY},
    }
    failing = {
        **passing,
        "ats": {"diff": ATS_NONINFERIORITY - 1e-6},
    }
    assert verdict(passing) == "SHIP"
    assert verdict(failing) == "NO-SHIP"
    assert verdict({**passing, "crps_gain": {"mean": -0.01, "lo": -0.2}}) == "NO-SHIP"


def test_bootstrap_interval_is_paired_and_seeded():
    samples = [1.0, -1.0, 0.5, 0.0]
    first = bootstrap_mean_ci(samples, seed=SEED, resamples=200)
    second = bootstrap_mean_ci(samples, seed=SEED, resamples=200)
    other = bootstrap_mean_ci(samples, seed=SEED + 1, resamples=200)
    assert first == second
    assert first["lo"] <= first["mean"] <= first["hi"]
    assert other["lo"] != first["lo"] or other["hi"] != first["hi"]
