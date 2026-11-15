"""Synthetic tests for the single-feature preregistration.

No schedule, play-by-play file, or shipped coefficient table is read.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.services.prediction_dist import crps_normal, norm_ppf
from research.nfl_single_feature.crps import per_game_crps
from research.nfl_single_feature.design import DesignError, assert_allowlist
from research.nfl_single_feature.evaluate import (
    assert_study_frame,
    base_replay_origin,
    confirmatory_look,
    exploratory_screen,
    grade_from_replay,
    is_confirmatory_kickoff,
    minimum_detectable_effect,
)
from research.nfl_single_feature.holm import (
    HolmError,
    assert_confirmatory_family,
    cluster_bootstrap_means,
    decide_feature,
    holm_stepdown,
    one_sided_p_negative,
    resampled_means,
    upper_bound,
)
from research.nfl_single_feature.neutral_pace import neutral_clock_deltas, neutral_pace_feature
from research.nfl_single_feature.ol_availability import ol_matchup_feature
from research.nfl_single_feature.pressure import (
    centered_interaction,
    effects_asof,
    filter_dropbacks,
    pressure_matchup_feature,
    residualize_rate,
    ridge_effects,
    season_centers,
)
from research.nfl_single_feature.specs import (
    BOOTSTRAP_DRAWS,
    CONFIRMATORY_SCHEDULE_CLUSTERS,
    CONFIRMATORY_SCHEDULE_N,
    FEATURES,
    HOLM_ALPHA_STAR,
    MARGIN_BASE,
    MARGIN_SIGMA,
    OL_CENTER_WEIGHT,
    PRESSURE_K_GAMES,
    TOTAL_BASE,
    TOTAL_SIGMA,
)

PACKAGE = Path(__file__).resolve().parents[2] / "research" / "nfl_single_feature"


def test_package_does_not_load_real_data_or_shipped_coefficients():
    banned = ("MARGIN_COEFS", "read_parquet", "read_csv", "urlopen", "nflreadr", "requests")
    for path in PACKAGE.glob("*.py"):
        text = path.read_text()
        for token in banned:
            assert token not in text, f"{path.name} contains {token}"


def test_allowlists_match_the_market_blind_base():
    assert "mkt_d" not in MARGIN_BASE
    assert "pass_d" not in MARGIN_BASE
    assert "plays_s" in TOTAL_BASE
    assert MARGIN_SIGMA == 13.5
    assert TOTAL_SIGMA == 10.0
    assert set(FEATURES) == {"pressure_x", "ol_d", "neutral_pace_sum"}
    with pytest.raises(DesignError):
        assert_allowlist(("epa_d", "spread_line"), MARGIN_BASE + ("spread_line",))
    with pytest.raises(DesignError):
        assert_allowlist(("pass_d",), MARGIN_BASE)


def test_per_game_crps_matches_the_production_formula():
    expected = np.array([0.0, 3.0, -7.0, 14.0])
    actual = np.array([0.0, 3.0, 10.0, -2.0])
    got = per_game_crps(expected, MARGIN_SIGMA, actual)
    for e, a, g in zip(expected, actual, got):
        assert g == pytest.approx(crps_normal(float(e), MARGIN_SIGMA, float(a)))
    assert got[0] < got[2]
    assert per_game_crps([1.0], 0.0, [4.0]) == pytest.approx([3.0])


def test_mde_uses_holm_alpha_star_and_power_one_half():
    z_alpha = norm_ppf(1.0 - HOLM_ALPHA_STAR)
    assert HOLM_ALPHA_STAR == pytest.approx(0.025 / 3)
    assert norm_ppf(0.5) == 0.0
    sd, n = 2.0, 100
    assert minimum_detectable_effect(sd, n) == pytest.approx(z_alpha * sd / np.sqrt(n))
    assert CONFIRMATORY_SCHEDULE_N == 31
    assert CONFIRMATORY_SCHEDULE_CLUSTERS == 2
    assert minimum_detectable_effect(1.0, CONFIRMATORY_SCHEDULE_N) == pytest.approx(
        z_alpha / np.sqrt(CONFIRMATORY_SCHEDULE_N),
    )


def test_holm_stops_after_the_first_miss_and_uses_the_family_of_three():
    rows = holm_stepdown({"ol_d": 0.02, "neutral_pace_sum": 0.001, "pressure_x": 0.02})
    by_name = {row["feature"]: row for row in rows}
    assert by_name["neutral_pace_sum"]["reject"] is True
    assert by_name["ol_d"]["reject"] is False
    assert by_name["pressure_x"]["reject"] is False
    assert by_name["pressure_x"]["threshold"] == pytest.approx(0.025)
    none = holm_stepdown({"a": 0.02, "b": 0.009, "c": 0.03})
    assert [row["reject"] for row in none] == [False, False, False]
    with pytest.raises(HolmError):
        assert_confirmatory_family({"pressure_x", "ol_d"})


def test_cluster_resample_keeps_games_in_a_week_together():
    means = resampled_means(
        np.array([10.0, 20.0]),
        np.array([2.0, 2.0]),
        np.array([[0, 0]]),
    )
    assert means == pytest.approx([5.0])
    delta = np.array([0.0, 10.0, 10.0, 10.0])
    boot = cluster_bootstrap_means(delta, [2016, 2016, 2016, 2016], [1, 1, 2, 2], n_draws=BOOTSTRAP_DRAWS)
    assert len(boot) == BOOTSTRAP_DRAWS
    again = cluster_bootstrap_means(delta, [2016, 2016, 2016, 2016], [1, 1, 2, 2])
    assert boot == pytest.approx(again)
    assert one_sided_p_negative(np.array([-1.0, -0.2])) == pytest.approx(1 / 3)
    assert upper_bound(np.array([-2.0, -1.0, 0.5, 1.0])) == pytest.approx(np.quantile(
        [-2.0, -1.0, 0.5, 1.0], 0.975, method="linear",
    ))


def test_decision_requires_holm_bound_and_sign():
    passed = decide_feature(feature="pressure_x", p_value=0.001, holm_reject=True, bound=-0.01, coef=-0.4)
    assert passed["pass"] is True
    assert decide_feature(
        feature="pressure_x", p_value=0.001, holm_reject=False, bound=-0.01, coef=-0.4,
    )["pass"] is False
    assert decide_feature(
        feature="pressure_x", p_value=0.001, holm_reject=True, bound=0.0, coef=-0.4,
    )["pass"] is False
    assert decide_feature(
        feature="ol_d", p_value=0.001, holm_reject=True, bound=-0.2, coef=0.2,
    )["pass"] is False
    assert decide_feature(
        feature="neutral_pace_sum", p_value=0.001, holm_reject=True, bound=-0.2, coef=0.0,
    )["pass"] is False


def _margin_games(seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for season, n in ((2015, 30), (2016, 12)):
        for i in range(n):
            epa = float(rng.normal())
            rows.append({
                "season": season,
                "week": (i % 6) + 1,
                "epa_d": epa,
                "pts_d": float(rng.normal()),
                "st_d": float(rng.normal()),
                "qb_d": float(rng.normal()),
                "hfa": float(i % 2),
                "pressure_x": float(rng.normal()),
                "margin": 3.0 * epa,
                "spread_line": float(rng.normal()),
            })
    return pd.DataFrame(rows)


def test_walk_forward_refits_and_ignores_the_test_season_and_the_market_column():
    games = _margin_games()
    replay = base_replay_origin(games, "pressure_x", 2016)
    assert replay is not None
    graded = grade_from_replay(replay)
    assert graded["base_coefficients"]["epa_d"] == pytest.approx(3.0, abs=1e-8)
    assert abs(graded["base_coefficients"]["epa_d"] - 6.266) > 1
    leaked = games.copy()
    leaked.loc[leaked["season"] == 2016, "margin"] = -500.0
    leaked["spread_line"] = 40.0
    replay_2 = base_replay_origin(leaked, "pressure_x", 2016)
    assert replay_2 is not None
    assert replay_2["graded"]["pred_base"].to_numpy() == pytest.approx(
        replay["graded"]["pred_base"].to_numpy(),
    )
    screen = exploratory_screen(games, "pressure_x")
    flipped = games.copy()
    flipped["pressure_x"] = games["pressure_x"] + 2.0
    flipped_screen = exploratory_screen(flipped, "pressure_x")
    assert screen["role"] == "exploratory_screen"
    assert screen["confirmatory"] is False
    assert screen["ship_gate"] is False
    assert screen["replay_sd"] == pytest.approx(flipped_screen["replay_sd"])
    assert screen["endpoint"] == "margin"
    posted = games.copy()
    posted["game_type"] = "REG"
    posted.loc[(posted["season"] == 2016) & (posted["week"] == 1), "game_type"] = "POST"
    posted_screen = exploratory_screen(posted, "pressure_x")
    graded_weeks = posted_screen["graded"]
    assert isinstance(graded_weeks, pd.DataFrame)
    assert not ((graded_weeks["season"] == 2016) & (graded_weeks["week"] == 1)).any()
    assert (graded_weeks["season"] == 2016).any()


def test_study_frame_rejects_the_spent_regime_and_protects_january_2027():
    early = pd.DataFrame({"season": [2012, 2016], "week": [1, 1]})
    with pytest.raises(DesignError):
        assert_study_frame(early, confirmatory=False)
    future = pd.DataFrame({
        "season": [2016, 2027],
        "week": [1, 1],
        "kickoff": ["2016-09-11 13:00", "2027-09-12 13:00"],
    })
    with pytest.raises(DesignError):
        assert_study_frame(future, confirmatory=False)
    next_regular = pd.DataFrame({
        "season": [2015, 2027],
        "week": [1, 1],
        "kickoff": ["2015-09-10 13:00", "2027-09-12 13:00"],
        "game_type": ["REG", "REG"],
    })
    with pytest.raises(DesignError):
        assert_study_frame(next_regular, confirmatory=True)
    december = pd.DataFrame({
        "season": [2026],
        "week": [17],
        "kickoff": ["2026-12-31 20:20"],
        "game_type": ["REG"],
    })
    with pytest.raises(DesignError):
        assert_study_frame(december, confirmatory=True)
    february = pd.DataFrame({
        "season": [2026],
        "week": [22],
        "kickoff": ["2027-02-14 18:30"],
        "game_type": ["SB"],
    })
    with pytest.raises(DesignError):
        assert_study_frame(february, confirmatory=True)
    held_out = pd.DataFrame({
        "season": [2016, 2026],
        "week": [1, 18],
        "kickoff": ["2016-09-11 13:00", "2027-01-10 13:00"],
    })
    with pytest.raises(DesignError):
        assert_study_frame(held_out, confirmatory=False)
    assert is_confirmatory_kickoff("2027-01-03 13:00")
    assert is_confirmatory_kickoff("2027-01-10 16:25")
    assert is_confirmatory_kickoff(pd.Timestamp("2027-01-01 05:30", tz="UTC"))
    assert not is_confirmatory_kickoff("2026-12-31 20:20")
    assert not is_confirmatory_kickoff(pd.Timestamp("2027-01-01 03:00", tz="UTC"))
    assert not is_confirmatory_kickoff("2027-02-14 18:30")
    assert not is_confirmatory_kickoff("2027-09-12 13:00")


def _pressure_plays() -> pd.DataFrame:
    rows = []
    specs = [
        {"wp": 0.50, "qb_dropback": 1, "keep": True},
        {"wp": 0.10, "qb_dropback": 1, "keep": True},
        {"wp": 0.90, "qb_dropback": 1, "keep": True},
        {"wp": 0.09, "qb_dropback": 1, "keep": False},
        {"wp": 0.91, "qb_dropback": 1, "keep": False},
        {"wp": np.nan, "qb_dropback": 1, "keep": False},
        {"wp": 0.50, "qb_kneel": 1, "keep": False},
        {"wp": 0.50, "qb_spike": 1, "keep": False},
        {"wp": 0.50, "qb_dropback": 0, "keep": False},
        {"wp": 0.50, "season": 2017, "keep": False},
        {"wp": 0.50, "season": 2012, "keep": False},
        {"wp": 0.50, "special_teams_play": 1, "keep": False},
    ]
    for i, spec in enumerate(specs):
        row = {
            "game_id": f"g{i}", "season": 2018, "week": 1, "posteam": "AAA", "defteam": "BBB",
            "qb_dropback": 1, "wp": 0.5, "qb_kneel": 0, "qb_spike": 0, "special_teams_play": 0,
            "pressure": 1, "qb_att": 2.4, "pass": 1, "rush": 0,
        }
        row.update(spec)
        row.pop("keep")
        rows.append(row)
    return pd.DataFrame(rows)


def test_dropback_filter_is_point_in_time_and_sack_dropbacks_stay():
    plays = _pressure_plays()
    kept = filter_dropbacks(plays)
    assert len(kept) == 3
    sack = plays.iloc[[0]].copy()
    sack["pass"] = 1
    sack["qb_dropback"] = 1
    sack["wp"] = 0.4
    assert len(filter_dropbacks(sack)) == 1


def test_pressure_interaction_is_antisymmetric_and_ignores_the_future_and_main_effects():
    assert centered_interaction(1.0, 1.0, 0.2, 0.4) == pytest.approx(-centered_interaction(0.2, 0.4, 1.0, 1.0))
    team_games = pd.DataFrame([
        {"season": 2018, "week": 1, "game_id": "w1a", "team": "AAA", "opponent": "BBB",
         "off_pressures": 10, "off_dropbacks": 40, "qb_att": 2.5},
        {"season": 2018, "week": 1, "game_id": "w1a", "team": "BBB", "opponent": "AAA",
         "off_pressures": 8, "off_dropbacks": 40, "qb_att": 2.6},
        {"season": 2018, "week": 2, "game_id": "w2a", "team": "AAA", "opponent": "BBB",
         "off_pressures": 20, "off_dropbacks": 40, "qb_att": 2.5},
        {"season": 2018, "week": 2, "game_id": "w2a", "team": "BBB", "opponent": "AAA",
         "off_pressures": 4, "off_dropbacks": 40, "qb_att": 2.9},
        {"season": 2018, "week": 2, "game_id": "w2b", "team": "CCC", "opponent": "DDD",
         "off_pressures": 20, "off_dropbacks": 40, "qb_att": 2.9},
        {"season": 2018, "week": 4, "game_id": "w4a", "team": "AAA", "opponent": "BBB",
         "off_pressures": 40, "off_dropbacks": 40, "qb_att": 9.0},
    ])
    games = pd.DataFrame([
        {"game_id": "w3", "season": 2018, "week": 3, "home": "AAA", "away": "BBB"},
    ])
    feature = pressure_matchup_feature(team_games, games)
    assert list(feature.columns) == ["game_id", "season", "week", "pressure_x"]
    assert np.isfinite(feature["pressure_x"].iloc[0])
    decoy = team_games.copy()
    decoy["qb_att_current"] = 0.1
    assert pressure_matchup_feature(decoy, games)["pressure_x"].iloc[0] == pytest.approx(
        feature["pressure_x"].iloc[0],
    )
    changed_att = team_games.copy()
    changed_att.loc[changed_att["week"] == 2, "qb_att"] = 4.0
    assert pressure_matchup_feature(changed_att, games)["pressure_x"].iloc[0] != pytest.approx(
        feature["pressure_x"].iloc[0],
    )
    centered = season_centers(team_games)
    week2 = centered[(centered["season"] == 2018) & (centered["week"] == 2)]
    assert week2["center"].iloc[0] == pytest.approx((10 / 40 + 8 / 40) / 2)


def test_residual_removes_time_to_throw_and_shrinkage_pulls_effects_in():
    att = np.array([2.0, 2.5, 3.0, 3.5])
    residual, _intercept, slope = residualize_rate(3.0 + 2.0 * att, att)
    assert slope == pytest.approx(2.0)
    assert residual == pytest.approx(np.zeros(4))
    history = pd.DataFrame({
        "team": ["AAA"],
        "opponent": ["BBB"],
        "resid": [1.0],
    })
    heavy_off, _heavy_def = ridge_effects(history, k_games=PRESSURE_K_GAMES)
    light_off, _light_def = ridge_effects(history, k_games=0.01)
    heavy_gap = heavy_off["AAA"] - heavy_off["BBB"]
    light_gap = light_off["AAA"] - light_off["BBB"]
    assert heavy_gap > 0
    assert heavy_gap < light_gap
    centered = season_centers(pd.DataFrame([
        {"season": 2018, "week": 1, "game_id": "a", "team": "AAA", "opponent": "BBB",
         "off_pressures": 4, "off_dropbacks": 20, "qb_att": 2.5},
        {"season": 2018, "week": 2, "game_id": "b", "team": "AAA", "opponent": "BBB",
         "off_pressures": 10, "off_dropbacks": 20, "qb_att": 2.5},
    ]))
    assert np.isnan(centered.loc[centered["week"] == 1, "center"]).all()
    assert effects_asof(centered, 2018, 1) is None


def _snap(season, week, team, player, position, snaps, team_snaps):
    return {
        "season": season, "week": week, "game_id": f"{season}-{week}-{team}",
        "team": team, "player_id": player, "position": position,
        "offense_snaps": snaps, "team_offense_snaps": team_snaps,
    }


def _five_starters(team: str, weeks: list[int], season: int = 2026) -> list[dict]:
    rows = []
    for week in weeks:
        for pos, player in (("LT", f"{team}-LT"), ("LG", f"{team}-LG"), ("C", f"{team}-C"),
                            ("RG", f"{team}-RG"), ("RT", f"{team}-RT")):
            rows.append(_snap(season, week, team, player, pos, 100, 100))
    return rows


def _games(home_qb="QB-H", away_qb="QB-A", prev=True):
    rows = []
    if prev:
        rows.append({
            "game_id": "prev", "season": 2026, "week": 4, "kickoff": "2026-10-04 17:00",
            "home": "HOM", "away": "AWY", "home_qb": home_qb, "away_qb": away_qb,
        })
    rows.append({
        "game_id": "cur", "season": 2026, "week": 5, "kickoff": "2026-10-11 17:00",
        "home": "HOM", "away": "AWY", "home_qb": home_qb, "away_qb": away_qb,
    })
    return pd.DataFrame(rows)


def _report(player, status, when, team="HOM"):
    return {
        "season": 2026, "week": 5, "team": team, "player_id": player,
        "report_status": status, "report_time": when,
    }


def test_ol_feature_counts_out_and_doubtful_not_questionable_or_who_played():
    snaps = _five_starters("HOM", [1, 2, 3, 4]) + _five_starters("AWY", [1, 2, 3, 4])
    snaps.append(_snap(2026, 5, "HOM", "HOM-C", "C", 0, 100))
    snaps.append(_snap(2026, 5, "HOM", "HOM-BACKUP", "C", 100, 100))
    injuries = pd.DataFrame([_report("HOM-C", "Out", "2026-10-11 12:00")])
    feature = ol_matchup_feature(pd.DataFrame(snaps), injuries, _games())
    current = feature[feature["game_id"] == "cur"].iloc[0]
    assert current["ol_exclude_reason"] == "ok"
    assert current["ol_d"] == pytest.approx(0.2 + OL_CENTER_WEIGHT)

    questionable = pd.DataFrame([_report("HOM-C", "Questionable", "2026-10-11 12:00")])
    q = ol_matchup_feature(pd.DataFrame(snaps), questionable, _games())
    assert q[q["game_id"] == "cur"]["ol_d"].iloc[0] == pytest.approx(0.0)

    late = pd.DataFrame([_report("HOM-C", "Out", "2026-10-11 18:00")])
    late_feature = ol_matchup_feature(pd.DataFrame(snaps), late, _games())
    assert late_feature[late_feature["game_id"] == "cur"]["ol_exclude_reason"].iloc[0] == "unverified_report"
    assert np.isnan(late_feature[late_feature["game_id"] == "cur"]["ol_d"].iloc[0])

    missing_time = pd.DataFrame([_report("HOM-C", "Out", None)])
    missing = ol_matchup_feature(pd.DataFrame(snaps), missing_time, _games())
    assert missing[missing["game_id"] == "cur"]["ol_exclude_reason"].iloc[0] == "unverified_report"


def test_ol_rolls_starters_forward_and_drops_qb_change_games():
    snaps = []
    for week in (1, 2, 3, 4):
        snaps.append(_snap(2026, week, "HOM", "OLD-C", "C", 100, 100))
        snaps.append(_snap(2026, week, "AWY", "AWY-C", "C", 100, 100))
    for week in (5, 6, 7, 8):
        snaps.append(_snap(2026, week, "HOM", "NEW-C", "C", 100, 100))
        snaps.append(_snap(2026, week, "AWY", "AWY-C", "C", 100, 100))
    injuries = pd.DataFrame([
        {"season": 2026, "week": 9, "team": "HOM", "player_id": "OLD-C",
         "report_status": "Injured Reserve", "report_time": "2026-11-08 12:00"},
    ])
    games = pd.DataFrame([{
        "game_id": "w9", "season": 2026, "week": 9, "kickoff": "2026-11-08 17:00",
        "home": "HOM", "away": "AWY", "home_qb": "QB1", "away_qb": "QB2",
    }])
    feature = ol_matchup_feature(pd.DataFrame(snaps), injuries, games)
    assert feature["ol_d"].iloc[0] == pytest.approx(0.0)

    early = games.copy()
    early["week"] = 5
    early["game_id"] = "w5"
    early["kickoff"] = "2026-10-11 17:00"
    early_inj = pd.DataFrame([
        {"season": 2026, "week": 5, "team": "HOM", "player_id": "OLD-C",
         "report_status": "IR", "report_time": "2026-10-11 12:00"},
    ])
    early_feature = ol_matchup_feature(pd.DataFrame(snaps), early_inj, early)
    assert early_feature["ol_d"].iloc[0] == pytest.approx(1.0 + OL_CENTER_WEIGHT)

    changed = _games(home_qb="QB-NEW")
    changed.loc[changed["game_id"] == "prev", "home_qb"] = "QB-OLD"
    healthy = ol_matchup_feature(
        pd.DataFrame(_five_starters("HOM", [1, 2, 3, 4]) + _five_starters("AWY", [1, 2, 3, 4])),
        pd.DataFrame(columns=["season", "week", "team", "player_id", "report_status", "report_time"]),
        changed,
    )
    assert healthy[healthy["game_id"] == "cur"]["ol_exclude_reason"].iloc[0] == "qb_change"


def test_ol_keeps_only_five_starters_and_ignores_a_sixth():
    snaps = []
    for week in (1, 2, 3, 4):
        snaps.append(_snap(2026, week, "HOM", "S1", "LT", 100, 100))
        snaps.append(_snap(2026, week, "HOM", "S2", "LG", 98, 100))
        snaps.append(_snap(2026, week, "HOM", "S3", "C", 96, 100))
        snaps.append(_snap(2026, week, "HOM", "S4", "RG", 94, 100))
        snaps.append(_snap(2026, week, "HOM", "S5", "RT", 92, 100))
        snaps.append(_snap(2026, week, "HOM", "S6", "G", 80, 100))
        for pos, player in (("LT", "A1"), ("LG", "A2"), ("C", "A3"), ("RG", "A4"), ("RT", "A5")):
            snaps.append(_snap(2026, week, "AWY", player, pos, 100, 100))
    injuries = pd.DataFrame([_report("S6", "Out", "2026-10-11 12:00")])
    feature = ol_matchup_feature(pd.DataFrame(snaps), injuries, _games())
    assert feature[feature["game_id"] == "cur"]["ol_d"].iloc[0] == pytest.approx(0.0)


def _pace_drive(team, season, week, gaps, **overrides):
    rebuilt = []
    clock = 3000.0
    for i in range(len(gaps) + 1):
        row = {
            "game_id": f"{season}-{week}-{team}",
            "season": season,
            "week": week,
            "qtr": 1,
            "posteam": team,
            "defteam": "OPP",
            "wp": 0.5,
            "score_differential": 0,
            "pass": 1.0,
            "rush": 0.0,
            "qb_kneel": 0.0,
            "qb_spike": 0.0,
            "special_teams_play": 0.0,
            "game_seconds_remaining": clock,
            "half_seconds_remaining": 800.0,
            "quarter_seconds_remaining": 800.0,
            "drive": 1,
            "temp": 70.0,
            "roof": "dome",
        }
        row.update(overrides)
        rebuilt.append(row)
        if i < len(gaps):
            clock -= gaps[i]
    return rebuilt


def test_neutral_pace_is_the_sum_of_prior_seconds_and_ignores_weather():
    gaps = [25.0] * 30
    plays = pd.DataFrame(
        _pace_drive("HOM", 2026, 1, gaps) + _pace_drive("AWY", 2026, 1, gaps),
    )
    games = pd.DataFrame([
        {"game_id": "w2", "season": 2026, "week": 2, "home": "HOM", "away": "AWY"},
    ])
    feature = neutral_pace_feature(plays, games)
    assert feature["neutral_pace_sum"].iloc[0] == pytest.approx(50.0)
    hot = plays.copy()
    hot["temp"] = 10.0
    hot["roof"] = "outdoors"
    hot["wind"] = 25.0
    assert neutral_pace_feature(hot, games)["neutral_pace_sum"].iloc[0] == pytest.approx(50.0)
    future = pd.concat([
        plays,
        pd.DataFrame(_pace_drive("HOM", 2026, 2, [10.0] * 30) + _pace_drive("AWY", 2026, 2, [10.0] * 30)),
    ], ignore_index=True)
    assert neutral_pace_feature(future, games)["neutral_pace_sum"].iloc[0] == pytest.approx(50.0)
    short = pd.DataFrame(_pace_drive("HOM", 2026, 1, [25.0] * 29) + _pace_drive("AWY", 2026, 1, gaps))
    assert np.isnan(neutral_pace_feature(short, games)["neutral_pace_sum"].iloc[0])


def test_neutral_filter_drops_the_preregistered_states_only():
    base = _pace_drive("HOM", 2026, 1, [25.0])
    assert len(neutral_clock_deltas(pd.DataFrame(base))) == 1
    q4 = _pace_drive("HOM", 2026, 1, [25.0], qtr=4)
    assert len(neutral_clock_deltas(pd.DataFrame(q4))) == 0
    blowout = _pace_drive("HOM", 2026, 1, [25.0], score_differential=8)
    assert len(neutral_clock_deltas(pd.DataFrame(blowout))) == 0
    tied = _pace_drive("HOM", 2026, 1, [25.0], score_differential=-7)
    assert len(neutral_clock_deltas(pd.DataFrame(tied))) == 1
    low_wp = _pace_drive("HOM", 2026, 1, [25.0], wp=0.19)
    edge_wp = _pace_drive("HOM", 2026, 1, [25.0], wp=0.20)
    assert len(neutral_clock_deltas(pd.DataFrame(low_wp))) == 0
    assert len(neutral_clock_deltas(pd.DataFrame(edge_wp))) == 1
    late = _pace_drive("HOM", 2026, 1, [25.0], qtr=2, half_seconds_remaining=120)
    just_out = _pace_drive("HOM", 2026, 1, [25.0], qtr=2, half_seconds_remaining=121)
    assert len(neutral_clock_deltas(pd.DataFrame(late))) == 0
    assert len(neutral_clock_deltas(pd.DataFrame(just_out))) == 1
    end_q1 = _pace_drive("HOM", 2026, 1, [25.0], qtr=1, quarter_seconds_remaining=30)
    assert len(neutral_clock_deltas(pd.DataFrame(end_q1))) == 1
    kneel = _pace_drive("HOM", 2026, 1, [25.0], qb_kneel=1)
    assert len(neutral_clock_deltas(pd.DataFrame(kneel))) == 0
    slow = _pace_drive("HOM", 2026, 1, [41.0])
    capped = _pace_drive("HOM", 2026, 1, [40.0])
    assert len(neutral_clock_deltas(pd.DataFrame(slow))) == 0
    assert len(neutral_clock_deltas(pd.DataFrame(capped))) == 1
    old = pd.DataFrame(_pace_drive("HOM", 2012, 1, [25.0] * 30))
    assert len(neutral_clock_deltas(old)) == 0


def _confirmatory_frame() -> pd.DataFrame:
    rng = np.random.default_rng(1)
    rows = []
    for week in range(1, 8):
        for i in range(2):
            rows.append(_study_row(2015, week, i, rng, kickoff=None))
    for week, kickoff in ((17, "2027-01-03 13:00"), (18, "2027-01-10 13:00")):
        for i in range(2):
            rows.append(_study_row(2026, week, i, rng, kickoff=kickoff))
    return pd.DataFrame(rows)


def _study_row(season: int, week: int, i: int, rng: np.random.Generator, kickoff: str | None) -> dict:
    return {
        "season": season,
        "week": week,
        "game_type": "REG",
        "kickoff": kickoff,
        "epa_d": float(rng.normal()),
        "pts_d": float(rng.normal()),
        "st_d": float(rng.normal()),
        "qb_d": float(rng.normal()),
        "hfa": float(i),
        "pts_s": float(rng.normal()),
        "epa_s": float(rng.normal()),
        "env2": float(rng.normal()),
        "plays_s": float(rng.normal()),
        "qb_s": float(rng.normal()),
        "pressure_x": float(rng.normal()),
        "ol_d": float(rng.normal()),
        "neutral_pace_sum": float(rng.normal() + 50),
        "margin": float(rng.normal()),
        "total": float(rng.normal() + 44),
    }


def test_confirmatory_look_is_january_2027_kickoffs_and_does_not_train_on_them():
    games = _confirmatory_frame()
    result = confirmatory_look(games)
    assert result["role"] == "confirmatory"
    assert result["confirmatory"] is True
    assert result["kickoff_window"] == "2027-01 America/New_York"
    assert result["schedule_n"] == 31
    assert result["schedule_clusters"] == 2
    assert result["schedule_mde_multiple"] == pytest.approx(
        minimum_detectable_effect(1.0, 31),
    )
    assert {row["feature"] for row in result["decisions"]} == set(FEATURES)
    leaked = games.copy()
    leaked.loc[leaked["season"] == 2026, "margin"] = 400.0
    leaked.loc[leaked["season"] == 2026, "total"] = -400.0
    base = base_replay_origin(games, "pressure_x", 2026, january_2027=True)
    base_leaked = base_replay_origin(leaked, "pressure_x", 2026, january_2027=True)
    assert base is not None and base_leaked is not None
    assert set(base["graded"]["week"]) == {17, 18}
    assert base_leaked["graded"]["pred_base"].to_numpy() == pytest.approx(
        base["graded"]["pred_base"].to_numpy(),
    )
    december = games.copy()
    december.loc[december["week"] == 17, "kickoff"] = "2026-12-31 20:20"
    with pytest.raises(DesignError):
        confirmatory_look(december)
    next_season = games.copy()
    next_season.loc[next_season["week"] == 18, "season"] = 2027
    next_season.loc[next_season["week"] == 18, "kickoff"] = "2027-09-12 13:00"
    with pytest.raises(DesignError):
        confirmatory_look(next_season)
    with pytest.raises(DesignError):
        exploratory_screen(games, "pressure_x")
