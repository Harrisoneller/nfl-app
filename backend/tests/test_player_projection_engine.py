"""Unit tests for the player projection engine (pure math, no DB/network).

Guards the v2 contract: priors weight recent seasons more, the Bayesian update
moves smoothly from prior to observed as games accrue, game-environment
multipliers respond in the right direction, distributions are truncated at 0,
and the season variance keeps the correlated talent component.
"""
from __future__ import annotations

import math

import pytest

from app.services import player_projection_engine as eng


# ---- Priors -----------------------------------------------------------------


def test_build_prior_weights_recent_season_more():
    recent_heavy = eng.build_prior(
        "receiving_yards",
        [{"mean": 90.0, "sd": 20.0, "games": 17},
         {"mean": 50.0, "sd": 20.0, "games": 17}],
    )
    assert recent_heavy is not None
    mean, _, _ = recent_heavy
    assert 70.0 < mean < 90.0  # pulled toward the recent 90, not the midpoint


def test_build_prior_injury_season_counts_less():
    full = eng.build_prior(
        "rushing_yards",
        [{"mean": 80.0, "sd": 20.0, "games": 17},
         {"mean": 40.0, "sd": 20.0, "games": 17}],
    )
    hurt = eng.build_prior(
        "rushing_yards",
        [{"mean": 80.0, "sd": 20.0, "games": 4},   # only 4 games at 80
         {"mean": 40.0, "sd": 20.0, "games": 17}],
    )
    assert full is not None and hurt is not None
    assert hurt[0] < full[0]  # the short season drags the prior less


def test_build_prior_empty_returns_none():
    assert eng.build_prior("targets", [None, None, None]) is None
    assert eng.build_prior("targets", []) is None


def test_build_prior_thin_history_lowers_confidence():
    thick = eng.build_prior(
        "targets", [{"mean": 8.0, "sd": 2.0, "games": 17}] * 2,
    )
    thin = eng.build_prior(
        "targets", [{"mean": 8.0, "sd": 2.0, "games": 4}],
    )
    assert thick is not None and thin is not None
    assert thin[2] < thick[2]  # fewer pseudo-games of prior evidence


def test_rookie_prior_tiers_ordered():
    d1 = eng.rookie_prior("receiving_yards", "WR", "day1")
    d3 = eng.rookie_prior("receiving_yards", "WR", "day3")
    assert d1 is not None and d3 is not None
    assert d1[0] > d3[0]
    assert eng.rookie_prior("passing_yards", "WR", "day1") is None  # not in kit


def test_age_multiplier_curves():
    assert eng.age_multiplier("RB", 25) == 1.0
    assert eng.age_multiplier("RB", 30) < 1.0
    assert eng.age_multiplier("QB", 30) == 1.0            # QBs age later
    assert eng.age_multiplier("RB", 30) < eng.age_multiplier("WR", 30)
    assert eng.age_multiplier("WR", 21) < 1.0             # pre-peak growth ramp
    assert eng.age_multiplier("RB", 40) >= 0.72           # clamped
    assert eng.age_multiplier("WR", None) == 1.0


def test_build_prior_regresses_toward_position_mean():
    # Thin history → pulled hard toward the positional mean; thick → lightly.
    thin = eng.build_prior(
        "receiving_yards", [{"mean": 120.0, "sd": 25.0, "games": 4}],
        position_mean=55.0,
    )
    thick = eng.build_prior(
        "receiving_yards", [{"mean": 120.0, "sd": 25.0, "games": 17}] * 3,
        position_mean=55.0,
    )
    none = eng.build_prior(
        "receiving_yards", [{"mean": 120.0, "sd": 25.0, "games": 17}] * 3,
    )
    assert thin is not None and thick is not None and none is not None
    assert thin[0] < thick[0] < none[0]  # regression strength ordered by evidence
    # Exact: thin = (4·120 + 3·55) / 7
    assert thin[0] == pytest.approx((4 * 120 + 3 * 55) / 7)


# ---- Role / depth-chart multipliers ---------------------------------------------


def test_role_multiplier_qb_winner_take_all():
    assert eng.role_multiplier("QB", 1) == 1.0
    assert eng.role_multiplier("QB", 2) <= 0.05  # Andy Dalton case
    assert eng.role_multiplier("QB", 4) <= 0.05


def test_role_multiplier_skill_positions_rotate():
    assert eng.role_multiplier("WR", 2) == 1.0        # WR2 is a full role
    assert eng.role_multiplier("RB", 2) > 0.5          # committees exist
    assert eng.role_multiplier("RB", 5) < eng.role_multiplier("RB", 2)
    assert eng.role_multiplier("TE", 2) < 1.0


def test_role_multiplier_unknown_depth_is_neutral():
    assert eng.role_multiplier("QB", None) == 1.0
    assert eng.role_multiplier("K", 2) == 1.0  # positions we don't model


def test_scale_posterior_scales_whole_distribution():
    post = eng.bayesian_update(250.0, 60.0, 8.0, None, None, 0.0)
    scaled = eng.scale_posterior(post, 0.05)
    assert scaled.mean == pytest.approx(post.mean * 0.05)
    assert scaled.game_sd == pytest.approx(post.game_sd * 0.05)
    assert scaled.talent_sd == pytest.approx(post.talent_sd * 0.05)


# ---- Bayesian update ----------------------------------------------------------


def test_bayesian_update_no_observation_returns_prior():
    post = eng.bayesian_update(60.0, 25.0, 8.0, None, None, 0.0)
    assert post.mean == pytest.approx(60.0)
    assert post.game_sd == pytest.approx(25.0)


def test_bayesian_update_shrinks_toward_observed_with_games():
    early = eng.bayesian_update(60.0, 25.0, 8.0, 100.0, 25.0, 2.0)
    late = eng.bayesian_update(60.0, 25.0, 8.0, 100.0, 25.0, 14.0)
    assert 60.0 < early.mean < late.mean < 100.0
    # weights are exact: (8*60 + 2*100) / 10
    assert early.mean == pytest.approx(68.0)


def test_bayesian_update_talent_sd_shrinks_with_evidence():
    early = eng.bayesian_update(60.0, 25.0, 8.0, 60.0, 25.0, 1.0)
    late = eng.bayesian_update(60.0, 25.0, 8.0, 60.0, 25.0, 15.0)
    assert late.talent_sd < early.talent_sd
    assert early.talent_sd < early.game_sd  # never more uncertain than one game


def test_bayesian_update_never_negative_mean():
    post = eng.bayesian_update(0.1, 0.5, 5.0, 0.0, 0.1, 10.0)
    assert post.mean >= 0.0
    assert post.game_sd > 0.0


# ---- Game coupling -------------------------------------------------------------


def test_env_multiplier_scoring_environment_direction():
    hot = eng.game_environment_multiplier(
        "receiving_tds", team_expected_pts=30.0, opp_expected_pts=30.0)
    cold = eng.game_environment_multiplier(
        "receiving_tds", team_expected_pts=14.0, opp_expected_pts=14.0)
    assert hot > 1.0 > cold


def test_env_multiplier_tds_more_elastic_than_volume():
    td = eng.game_environment_multiplier(
        "rushing_tds", team_expected_pts=30.0, opp_expected_pts=30.0)
    vol = eng.game_environment_multiplier(
        "carries", team_expected_pts=30.0, opp_expected_pts=30.0)
    assert td > vol > 1.0


def test_env_multiplier_game_script_tilt():
    # Big favorite (+10 margin): rush up, pass down. Neutral defense/env at 22.
    fav_rush = eng.game_environment_multiplier(
        "carries", team_expected_pts=27.0, opp_expected_pts=17.0)
    fav_pass = eng.game_environment_multiplier(
        "attempts", team_expected_pts=27.0, opp_expected_pts=17.0)
    dog_pass = eng.game_environment_multiplier(
        "attempts", team_expected_pts=17.0, opp_expected_pts=27.0)
    assert fav_rush > fav_pass
    assert dog_pass > fav_pass  # trailing teams throw


def test_env_multiplier_total_clamped():
    # Compounded worst case (terrible offense, elite defense, bad script) must
    # not exceed the overall clamp — market never prices ±40% game swings.
    worst = eng.game_environment_multiplier(
        "rushing_tds", team_expected_pts=12.0, opp_expected_pts=32.0,
        defense_factor=0.75)
    best = eng.game_environment_multiplier(
        "receiving_tds", team_expected_pts=33.0, opp_expected_pts=12.0,
        defense_factor=1.30)
    assert worst >= 0.75 - 1e-9
    assert best <= 1.30 + 1e-9


def test_env_multiplier_defense_factor_clamped():
    hi = eng.game_environment_multiplier(
        "receiving_yards", team_expected_pts=22.0, opp_expected_pts=22.0,
        defense_factor=5.0)
    lo = eng.game_environment_multiplier(
        "receiving_yards", team_expected_pts=22.0, opp_expected_pts=22.0,
        defense_factor=0.01)
    assert hi <= 1.30 + 1e-9
    assert lo >= 0.75 - 1e-9


# ---- Distributions --------------------------------------------------------------


def test_stat_over_prob_monotonic_in_line():
    probs = [eng.stat_over_prob(70.0, 25.0, line) for line in (40, 60, 70, 90, 120)]
    assert all(a > b for a, b in zip(probs, probs[1:]))


def test_stat_over_prob_half_at_mean_when_far_from_zero():
    assert eng.stat_over_prob(200.0, 20.0, 200.0) == pytest.approx(0.5, abs=0.01)


def test_stat_over_prob_truncation_at_zero():
    # A plain normal would say P(>0) < 1 for mean 2, sd 3; truncated must be 1.
    assert eng.stat_over_prob(2.0, 3.0, 0.0) == pytest.approx(1.0)
    # And truncation should RAISE P(over) vs the naive normal for low lines.
    naive = 1.0 - 0.5 * (1.0 + math.erf((1.0 - 2.0) / (3.0 * math.sqrt(2))))
    assert eng.stat_over_prob(2.0, 3.0, 1.0) > naive


def test_stat_interval_floors_at_zero():
    lo, hi = eng.stat_interval(5.0, 10.0, 0.8)
    assert lo == 0.0
    assert hi > 5.0


def test_anytime_td_prob():
    assert eng.anytime_td_prob(0.0) == 0.0
    assert eng.anytime_td_prob(0.7) == pytest.approx(1 - math.exp(-0.7))
    assert 0 < eng.anytime_td_prob(0.3) < eng.anytime_td_prob(0.9) < 1.0


# ---- Season aggregation -----------------------------------------------------------


def test_aggregate_season_mean_is_sum():
    agg = eng.aggregate_season([50.0, 60.0, 70.0], game_sd=20.0, talent_sd=5.0)
    assert agg["mean"] == pytest.approx(180.0)
    assert agg["games"] == 3


def test_aggregate_season_variance_decomposition():
    g, game_sd, talent_sd = 10, 20.0, 5.0
    agg = eng.aggregate_season([50.0] * g, game_sd=game_sd, talent_sd=talent_sd)
    expected_sd = math.sqrt(g**2 * talent_sd**2 + g * game_sd**2)
    assert agg["sd"] == pytest.approx(expected_sd)
    # The correlated talent term must make the band wider than independent noise.
    independent_only = math.sqrt(g * game_sd**2)
    assert agg["sd"] > independent_only


def test_aggregate_season_empty():
    agg = eng.aggregate_season([], game_sd=10.0, talent_sd=2.0)
    assert agg["mean"] == 0.0 and agg["sd"] == 0.0


def test_season_quantiles_ordered():
    qs = eng.season_quantiles(1000.0, 150.0)
    assert qs["p10"] < qs["p25"] < qs["p50"] < qs["p75"] < qs["p90"]
    assert qs["p50"] == pytest.approx(1000.0, abs=0.5)


# ---- Fantasy scoring ----------------------------------------------------------------


def test_fantasy_points_formats():
    stats = {
        "passing_yards": 250.0, "passing_tds": 2.0, "interceptions": 1.0,
        "rushing_yards": 20.0, "rushing_tds": 0.0,
        "receiving_yards": 0.0, "receiving_tds": 0.0, "receptions": 0.0,
    }
    # 250*.04 + 2*4 - 2 + 20*.1 = 10 + 8 - 2 + 2 = 18
    assert eng.fantasy_points(stats, "ppr") == pytest.approx(18.0)

    wr = {"receiving_yards": 80.0, "receiving_tds": 1.0, "receptions": 6.0}
    assert eng.fantasy_points(wr, "ppr") == pytest.approx(8 + 6 + 6)
    assert eng.fantasy_points(wr, "half_ppr") == pytest.approx(8 + 6 + 3)
    assert eng.fantasy_points(wr, "standard") == pytest.approx(8 + 6)


def test_fantasy_sd_combines_components():
    sds = {"receiving_yards": 30.0, "receptions": 2.0, "receiving_tds": 0.5}
    sd_ppr = eng.fantasy_sd(sds, "ppr")
    expected = math.sqrt((0.1 * 30) ** 2 + (1.0 * 2) ** 2 + (6.0 * 0.5) ** 2)
    assert sd_ppr == pytest.approx(expected)
    assert eng.fantasy_sd(sds, "standard") < sd_ppr  # receptions drop out
