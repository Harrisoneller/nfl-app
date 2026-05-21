"""Unit tests for the probabilistic prediction primitives.

Pure-math, no fixtures or DB — these guard the distribution engine that the
game predictor, season simulation, and backtest all rely on.
"""
import math

import pytest

from app.services import prediction_dist as pd


def test_norm_cdf_basics():
    assert pd.norm_cdf(0.0) == pytest.approx(0.5)
    assert pd.norm_cdf(1.96) == pytest.approx(0.975, abs=1e-3)
    # Symmetry
    assert pd.norm_cdf(-1.0) == pytest.approx(1.0 - pd.norm_cdf(1.0), abs=1e-9)


def test_norm_ppf_inverts_cdf():
    for p in (0.025, 0.1, 0.5, 0.9, 0.975):
        assert pd.norm_cdf(pd.norm_ppf(p)) == pytest.approx(p, abs=1e-6)
    assert pd.norm_ppf(0.975) == pytest.approx(1.95996, abs=1e-3)


def test_win_prob_pickem_and_monotonic():
    assert pd.win_prob(0.0) == pytest.approx(0.5)
    # One SD of margin favored ~ 84%
    assert pd.win_prob(pd.NFL_MARGIN_SIGMA) == pytest.approx(0.8413, abs=1e-3)
    # Monotonic increasing in expected margin
    assert pd.win_prob(3.0) < pd.win_prob(7.0) < pd.win_prob(14.0)
    # Symmetry around 0
    assert pd.win_prob(-7.0) == pytest.approx(1.0 - pd.win_prob(7.0), abs=1e-9)


def test_cover_equals_win_at_pickem_line():
    # Covering a line of 0 is exactly winning.
    for mu in (-10.0, -3.0, 0.0, 4.5, 12.0):
        assert pd.cover_prob_home(mu, 0.0) == pytest.approx(pd.win_prob(mu), abs=1e-12)


def test_cover_prob_direction():
    # Home favored by 5 (line -3): laying 3 of a 5-point edge -> just over 50%.
    p = pd.cover_prob_home(5.0, -3.0)
    assert 0.5 < p < 0.6
    # Getting +3 as a 5-point underdog -> well over 50% to cover.
    assert pd.cover_prob_home(-5.0, 3.0) == pytest.approx(1.0 - p, abs=1e-9)


def test_over_prob_direction():
    assert pd.over_prob(45.0, 45.0) == pytest.approx(0.5)
    assert pd.over_prob(50.0, 45.0) > 0.5
    assert pd.over_prob(40.0, 45.0) < 0.5


def test_margin_interval_widths():
    lo, hi = pd.margin_interval(3.0, sigma=13.5, level=0.8)
    # 80% interval is mean ± 1.2816 * sigma
    assert lo == pytest.approx(3.0 - 1.28155 * 13.5, abs=0.05)
    assert hi == pytest.approx(3.0 + 1.28155 * 13.5, abs=0.05)
    # Wider interval for higher level
    lo2, hi2 = pd.margin_interval(3.0, sigma=13.5, level=0.95)
    assert (hi2 - lo2) > (hi - lo)


def test_crps_normal_properties():
    sigma = 13.5
    # CRPS at the mean has the known closed-form value sigma * (sqrt(2)-1)/sqrt(pi)?
    # Numerically: 2*phi(0) - 1/sqrt(pi) = 0.79788 - 0.56419 = 0.23369
    at_mean = pd.crps_normal(0.0, sigma, 0.0)
    assert at_mean == pytest.approx(sigma * 0.233696, abs=1e-2)
    # CRPS grows as the observation moves away from the mean
    assert pd.crps_normal(0.0, sigma, 0.0) < pd.crps_normal(0.0, sigma, 20.0)
    # A sharper (smaller sigma) forecast that is correct scores better
    assert pd.crps_normal(0.0, 5.0, 0.0) < pd.crps_normal(0.0, 20.0, 0.0)
    # ...but an overconfident wrong forecast is punished
    assert pd.crps_normal(0.0, 2.0, 25.0) > pd.crps_normal(0.0, 13.5, 25.0)


def test_log_loss_and_brier():
    # Confident + correct -> near zero; confident + wrong -> large
    assert pd.log_loss(0.99, 1) < 0.02
    assert pd.log_loss(0.01, 1) > 4.0
    # 50/50 guess
    assert pd.log_loss(0.5, 1) == pytest.approx(math.log(2), abs=1e-9)
    assert pd.brier(0.5, 1) == pytest.approx(0.25)
    assert pd.brier(1.0, 1) == 0.0


def test_push_prob_key_numbers():
    assert pd.push_prob(-3.0) == pytest.approx(0.094)   # 3 is the biggest key number
    assert pd.push_prob(-3.5) == 0.0                    # half-point can't push
    assert pd.push_prob(-2.0) < pd.push_prob(-3.0)
