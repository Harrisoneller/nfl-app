"""Phase-1 context layer: game-conditional sigma + context arithmetic.

These tests are deliberately about *properties* rather than golden numbers.
The constants in ``dist_model`` are inherited from the CFB port and still need
fitting against NFL games (``fit_sigma_model``); pinning them here would make
that fit fail a test rather than improve the model. What must not change is the
shape: which direction each term moves sigma, that defaults stay a no-op, and
that nothing can produce a degenerate distribution.
"""
from __future__ import annotations

import math

from app.services import context_service, dist_model


# ---- Backward compatibility -------------------------------------------------


def test_new_arguments_default_to_no_op():
    """Adding context/wind must not move a game that has neither."""
    base = dist_model.margin_sigma_for_game(3.0, 45.0)
    with_defaults = dist_model.margin_sigma_for_game(
        3.0, 45.0, context_sigma_mult=1.0, wind_mph=None, indoor=False,
    )
    assert base == with_defaults

    t_base = dist_model.total_sigma_for_game(45.0)
    assert t_base == dist_model.total_sigma_for_game(
        45.0, context_sigma_mult=1.0, wind_mph=None, indoor=False,
    )


def test_unknown_wind_prices_like_no_wind():
    """A missing forecast is not a calm day and not an average day."""
    assert dist_model.wind_total_points(None) == 0.0
    assert dist_model.wind_total_points(0) == 0.0
    # Below the threshold, wind is ignored entirely.
    assert dist_model.wind_total_points(5.0) == 0.0


# ---- Sigma responds in the right direction ----------------------------------


def test_sigma_rises_with_scoring_environment():
    low = dist_model.margin_sigma_for_game(0.0, 36.0)
    high = dist_model.margin_sigma_for_game(0.0, 54.0)
    assert high > low


def test_sigma_rises_with_mismatch():
    pickem = dist_model.margin_sigma_for_game(0.0, 45.0)
    blowout = dist_model.margin_sigma_for_game(17.0, 45.0)
    assert blowout > pickem


def test_sigma_is_symmetric_in_favorite():
    """Only the magnitude of the mismatch matters, not who is favored."""
    assert dist_model.margin_sigma_for_game(10.0, 45.0) == \
        dist_model.margin_sigma_for_game(-10.0, 45.0)


def test_context_uncertainty_widens_margin_sigma():
    calm = dist_model.margin_sigma_for_game(3.0, 45.0, context_sigma_mult=1.0)
    unsure = dist_model.margin_sigma_for_game(3.0, 45.0, context_sigma_mult=1.15)
    assert unsure > calm


def test_thin_evidence_widens():
    """Week 1 must not be quoted with Week 12's precision."""
    known = dist_model.margin_sigma_for_game(3.0, 45.0, sample_scale=1.0)
    unknown = dist_model.margin_sigma_for_game(3.0, 45.0, sample_scale=0.0)
    assert unknown > known


def test_wind_compresses_rather_than_widens():
    """The counterintuitive one: wind makes a game tighter, not more random."""
    calm = dist_model.margin_sigma_for_game(3.0, 45.0, wind_mph=0.0)
    windy = dist_model.margin_sigma_for_game(3.0, 45.0, wind_mph=25.0)
    assert windy < calm

    calm_t = dist_model.total_sigma_for_game(45.0, wind_mph=0.0)
    windy_t = dist_model.total_sigma_for_game(45.0, wind_mph=25.0)
    assert windy_t < calm_t


def test_wind_removes_points_from_the_total_only_outdoors():
    assert dist_model.wind_total_points(25.0) < 0
    assert dist_model.wind_total_points(25.0, indoor=True) == 0.0


def test_wind_effect_is_capped():
    """A garbage forecast must not run away with the total."""
    at_cap = dist_model.wind_total_points(dist_model.WIND_MAX_EFFECT_MPH)
    absurd = dist_model.wind_total_points(200.0)
    assert absurd == at_cap


def test_sigma_stays_inside_bounds_under_abuse():
    lo, hi = dist_model._SIGMA_M_BOUNDS
    extremes = [
        dist_model.margin_sigma_for_game(0.0, 1.0, context_sigma_mult=0.0,
                                         sample_scale=1.0),
        dist_model.margin_sigma_for_game(99.0, 120.0, context_sigma_mult=99.0,
                                         rating_sd_pts=99.0, sample_scale=0.0),
    ]
    for s in extremes:
        assert lo <= s <= hi
        assert math.isfinite(s)


# ---- The joint distribution is internally consistent ------------------------


def test_win_prob_equals_cover_at_pickem():
    gd = dist_model.build_game_distribution(4.5, 45.0)
    assert abs(gd.win_prob() - gd.cover_prob_home(0.0)) < 1e-9


def test_margin_pmf_is_a_distribution():
    gd = dist_model.build_game_distribution(3.0, 45.0)
    pmf = gd.margin_pmf()
    assert abs(sum(pmf.values()) - 1.0) < 1e-6
    assert all(p >= 0 for p in pmf.values())


def test_discrete_cover_partitions_probability():
    gd = dist_model.build_game_distribution(-2.5, 47.0)
    win, push, lose = gd.cover_prob_discrete(-3.0)
    assert abs(win + push + lose - 1.0) < 1e-6
    # A key number must carry real push mass.
    assert push > 0.02


def test_half_point_lines_cannot_push():
    gd = dist_model.build_game_distribution(3.0, 45.0)
    assert gd.push_prob(-3.5) == 0.0


def test_favorite_side_is_the_more_dispersed_score():
    """What an independent margin/total model structurally gets wrong."""
    gd = dist_model.build_game_distribution(10.0, 48.0)
    _, home_sd = gd.team_score_params(home=True)
    _, away_sd = gd.team_score_params(home=False)
    assert gd.rho > 0
    assert home_sd > away_sd


def test_correlated_parlay_is_not_the_product_of_its_legs():
    """The whole reason the joint object exists."""
    gd = dist_model.build_game_distribution(9.0, 48.0)
    joint = gd.joint_prob(home_covers=-7.0, over=45.0)
    independent = gd.cover_prob_home(-7.0) * gd.over_prob(45.0)
    assert joint > independent + 0.005


def test_joint_prob_reduces_to_the_marginals():
    gd = dist_model.build_game_distribution(6.0, 44.0)
    assert abs(gd.joint_prob(home_covers=-3.0) - gd.cover_prob_home(-3.0)) < 1e-9
    assert abs(gd.joint_prob(over=44.0) - gd.over_prob(44.0)) < 1e-9


def test_meta_exposes_the_flat_comparison():
    gd = dist_model.build_game_distribution(3.0, 45.0)
    assert "flat_sigma_would_be" in gd.meta
    assert gd.meta["margin_sigma"] > 0


# ---- Context arithmetic -----------------------------------------------------


def _bundle(home_pts=0.0, away_pts=0.0, home_sigma=1.0, away_sigma=1.0):
    return {
        "season": 2026, "week": 5, "applicable": True, "matchup": {},
        "teams": {
            "KC": {"team_id": "KC", "points": home_pts, "sigma_mult": home_sigma,
                   "confidence": 0.9, "components": [
                       {"component": "availability", "points": home_pts,
                        "sigma_mult": home_sigma, "confidence": 0.9,
                        "source": "nflverse", "detail": {}}]},
            "LV": {"team_id": "LV", "points": away_pts, "sigma_mult": away_sigma,
                   "confidence": 0.9, "components": [
                       {"component": "availability", "points": away_pts,
                        "sigma_mult": away_sigma, "confidence": 0.9,
                        "source": "nflverse", "detail": {}}]},
        },
    }


def test_empty_context_is_inert():
    for bundle in (None, {}, {"applicable": False}):
        ctx = context_service.game_context(bundle, "KC", "LV")
        assert ctx["points"] == 0.0
        assert ctx["sigma_mult"] == 1.0
        assert ctx["applicable"] is False


def test_context_is_signed_from_the_home_side():
    """Home hurt = negative; away hurt = positive. Getting this backwards is
    the single easiest way to ship a model that is confidently wrong."""
    home_hurt = context_service.game_context(_bundle(home_pts=-3.0), "KC", "LV")
    assert home_hurt["points"] < 0

    away_hurt = context_service.game_context(_bundle(away_pts=-3.0), "KC", "LV")
    assert away_hurt["points"] > 0


def test_equal_damage_on_both_sides_nets_to_zero_but_widens():
    ctx = context_service.game_context(
        _bundle(home_pts=-3.0, away_pts=-3.0, home_sigma=1.05, away_sigma=1.05),
        "KC", "LV",
    )
    assert abs(ctx["points"]) < 1e-9
    # Uncertainty compounds regardless of which side carries it.
    assert ctx["sigma_mult"] > 1.0


def test_context_points_are_capped():
    ctx = context_service.game_context(_bundle(home_pts=-40.0), "KC", "LV")
    assert ctx["capped"] is True
    assert abs(ctx["points"]) < abs(ctx["points_uncapped"])
    assert abs(ctx["points"]) <= 21.0  # registry hard max on context.game_cap_pts


def test_sigma_multiplier_is_capped_and_never_shrinks():
    ctx = context_service.game_context(
        _bundle(home_sigma=5.0, away_sigma=5.0), "KC", "LV")
    assert 1.0 <= ctx["sigma_mult"] <= 1.60
    # Context may express uncertainty, never false precision.
    calm = context_service.game_context(
        _bundle(home_sigma=0.1, away_sigma=0.1), "KC", "LV")
    assert calm["sigma_mult"] >= 1.0


def test_context_points_helper_reads_one_side():
    b = _bundle(home_pts=-2.0, away_pts=1.0)
    assert context_service.context_points(b, "KC") == -2.0
    assert context_service.context_points(b, "LV") == 1.0
    assert context_service.context_points(b, "NE") == 0.0
    assert context_service.context_points(None, "KC") == 0.0


# ---- Storage round-trip: persist -> resolve -> apply -------------------------


def _session():
    """A throwaway SQLite session with only the context tables."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base
    from app.models.team_context import PlayerAvailability, TeamContextSnapshot

    engine = create_engine("sqlite://")
    for model in (PlayerAvailability, TeamContextSnapshot):
        model.__table__.create(engine)
    return sessionmaker(bind=engine)()


def test_persist_then_resolve_round_trip():
    db = _session()
    written = context_service.persist(
        db, season=2026, week=5,
        rows=[
            {"team_id": "KC", "component": "availability", "points": -2.5,
             "sigma_mult": 1.05, "confidence": 0.9, "source": "nflverse",
             "detail": {"players": ["QB1"]}},
            {"team_id": "LV", "component": "situational", "points": -0.8,
             "confidence": 1.0, "source": "derived"},
        ],
    )
    assert written == 2

    bundle = context_service.week_context(db, 2026, 5)
    assert bundle["applicable"] is True
    assert bundle["teams"]["KC"]["points"] == -2.5
    assert bundle["teams"]["LV"]["points"] == -0.8

    ctx = context_service.game_context(bundle, "KC", "LV")
    # KC is -2.5 and LV is -0.8, so the net favors... LV by 1.7.
    assert abs(ctx["points"] - (-1.7)) < 1e-6
    assert ctx["sigma_mult"] > 1.0


def test_source_priority_beats_recency():
    """An admin entry outranks a newer scraped one — a human looked at it."""
    from datetime import datetime, timedelta, timezone

    db = _session()
    now = datetime.now(timezone.utc)
    context_service.persist(
        db, season=2026, week=5, captured_at=now - timedelta(hours=2),
        rows=[{"team_id": "KC", "component": "availability", "points": -6.0,
               "source": "admin"}],
    )
    context_service.persist(
        db, season=2026, week=5, captured_at=now,
        rows=[{"team_id": "KC", "component": "availability", "points": -1.0,
               "source": "espn"}],
    )
    bundle = context_service.week_context(db, 2026, 5)
    assert bundle["teams"]["KC"]["points"] == -6.0


def test_newest_row_wins_within_a_source():
    from datetime import datetime, timedelta, timezone

    db = _session()
    now = datetime.now(timezone.utc)
    for pts, age in ((-5.0, 3), (-2.0, 0)):
        context_service.persist(
            db, season=2026, week=5, captured_at=now - timedelta(hours=age),
            rows=[{"team_id": "KC", "component": "availability", "points": pts,
                   "source": "espn"}],
        )
    bundle = context_service.week_context(db, 2026, 5)
    assert bundle["teams"]["KC"]["points"] == -2.0


def test_as_of_does_not_leak_the_future():
    """The property the whole append-only design exists for.

    A backtest pinned to Wednesday must not see Sunday's inactives.
    """
    from datetime import datetime, timedelta, timezone

    db = _session()
    now = datetime.now(timezone.utc)
    wednesday = now - timedelta(days=4)
    context_service.persist(
        db, season=2026, week=5, captured_at=wednesday,
        rows=[{"team_id": "KC", "component": "availability", "points": -1.0,
               "source": "espn"}],
    )
    context_service.persist(
        db, season=2026, week=5, captured_at=now,
        rows=[{"team_id": "KC", "component": "availability", "points": -7.0,
               "source": "espn"}],
    )
    # Pinned to Wednesday + an hour: only the early observation exists.
    early = context_service.week_context(
        db, 2026, 5, as_of=wednesday + timedelta(hours=1))
    assert early["teams"]["KC"]["points"] == -1.0

    live = context_service.week_context(db, 2026, 5)
    assert live["teams"]["KC"]["points"] == -7.0


def test_low_confidence_rows_are_dropped_not_discounted():
    db = _session()
    context_service.persist(
        db, season=2026, week=5,
        rows=[{"team_id": "KC", "component": "availability", "points": -9.0,
               "confidence": 0.01, "source": "news_llm"}],
    )
    bundle = context_service.week_context(db, 2026, 5)
    assert "KC" not in bundle["teams"]


def test_manual_entry_requires_a_note():
    import pytest

    db = _session()
    with pytest.raises(ValueError):
        context_service.record_manual(
            db, season=2026, week=5, team_id="KC", points=-2.0, note="   ")

    assert context_service.record_manual(
        db, season=2026, week=5, team_id="KC", points=-2.0,
        note="LT ruled out Friday") == 1
    bundle = context_service.week_context(db, 2026, 5)
    assert bundle["teams"]["KC"]["points"] == -2.0


def test_matchup_components_are_not_applied_league_wide():
    """A scheme edge against LV must not follow KC into every other game."""
    db = _session()
    context_service.persist(
        db, season=2026, week=5,
        rows=[{"team_id": "KC", "opponent_id": "LV",
               "component": "scheme_matchup", "points": 1.5,
               "source": "derived"}],
    )
    bundle = context_service.week_context(db, 2026, 5)
    assert bundle["teams"] == {}          # not standing context
    assert bundle["matchup"]["KC|LV"]     # matchup-scoped only

    vs_lv = context_service.game_context(bundle, "KC", "LV")
    assert abs(vs_lv["points"] - 1.5) < 1e-6

    vs_ne = context_service.game_context(bundle, "KC", "NE")
    assert vs_ne["points"] == 0.0

    # Same edge, read from the away side: sign must flip into home terms.
    away = context_service.game_context(bundle, "LV", "KC")
    assert abs(away["points"] - (-1.5)) < 1e-6


def test_empty_table_is_not_an_error():
    db = _session()
    bundle = context_service.week_context(db, 2026, 5)
    assert bundle["applicable"] is False
    assert context_service.game_context(bundle, "KC", "LV")["points"] == 0.0
