"""Synthetic tests for the single-feature preregistration.

No schedule, play-by-play file, or shipped coefficient table is read.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.services.prediction_dist import crps_normal, norm_ppf
from research.nfl_single_feature.calendar import is_january_2027_kickoff
from research.nfl_single_feature.crps import per_game_crps
from research.nfl_single_feature.design import DesignError, assert_allowlist
from research.nfl_single_feature.evaluate import (
    base_replay_origin,
    confirmatory_look,
    exploratory_screen,
    grade_from_replay,
    harm_check,
    minimum_detectable_effect,
    require_schedule_fields,
    run_stages,
    screen_retain,
)
from research.nfl_single_feature.holm import (
    WEBB_WEIGHTS,
    HolmError,
    cluster_robust_t,
    holm_stepdown,
    wild_cluster_replicates,
)
from research.nfl_single_feature.neutral_pace import neutral_clock_deltas, neutral_pace_feature
from research.nfl_single_feature.ol_availability import ol_matchup_feature
from research.nfl_single_feature.pressure import (
    assert_pressure_not_season_grain,
    centered_interaction,
    effects_asof,
    filter_dropbacks,
    pressure_matchup_feature,
    residualize_rate,
    ridge_effects,
    season_centers,
)
from research.nfl_single_feature.specs import (
    ATS_LB_LIMIT,
    BOOTSTRAP_DRAWS,
    CONFIRM_CLUSTERS_EXPECTED,
    CONFIRM_N_EXPECTED,
    FEATURES,
    HARM_CLUSTERS_EXPECTED,
    HARM_N_EXPECTED,
    HARM_UB_LIMIT,
    HOLM_ALPHA_STAR,
    MARGIN_BASE,
    MARGIN_SIGMA,
    OL_CENTER_WEIGHT,
    POWER,
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


def test_mde_reports_eighty_percent_power():
    z_alpha = norm_ppf(1.0 - HOLM_ALPHA_STAR)
    z_power = norm_ppf(POWER)
    assert POWER == pytest.approx(0.80)
    assert HOLM_ALPHA_STAR == pytest.approx(0.025 / 3)
    assert minimum_detectable_effect(1.0, CONFIRM_N_EXPECTED) == pytest.approx(
        (z_alpha + z_power) / np.sqrt(CONFIRM_N_EXPECTED),
    )
    assert CONFIRM_N_EXPECTED == 272
    assert CONFIRM_CLUSTERS_EXPECTED == 18
    assert HARM_N_EXPECTED == 43
    assert HARM_CLUSTERS_EXPECTED == 5


def test_holm_family_size_is_whoever_reached_confirmation():
    rows = holm_stepdown({"ol_d": 0.03, "pressure_x": 0.001})
    by_name = {row["feature"]: row for row in rows}
    assert by_name["pressure_x"]["threshold"] == pytest.approx(0.025 / 2)
    assert by_name["pressure_x"]["reject"] is True
    assert by_name["ol_d"]["reject"] is False
    alone = holm_stepdown({"neutral_pace_sum": 0.02})
    assert alone[0]["threshold"] == pytest.approx(0.025)
    assert alone[0]["reject"] is True
    with pytest.raises(HolmError):
        holm_stepdown({"not_a_feature": 0.01})


def test_webb_weights_and_wild_cluster_draws():
    assert len(WEBB_WEIGHTS) == 6
    assert WEBB_WEIGHTS.mean() == pytest.approx(0.0)
    assert np.mean(WEBB_WEIGHTS ** 2) == pytest.approx(1.0)
    values = np.array([-1.0, -1.0, -0.5, -0.5])
    draws = wild_cluster_replicates(values, [2016, 2016, 2016, 2016], [1, 1, 2, 2])
    again = wild_cluster_replicates(values, [2016, 2016, 2016, 2016], [1, 1, 2, 2])
    assert draws == pytest.approx(again)
    assert len(draws) == BOOTSTRAP_DRAWS
    robust = cluster_robust_t(values, [2016, 2016, 2016, 2016], [1, 1, 2, 2])
    assert robust["df"] == pytest.approx(1.0)


def test_screen_retain_drops_on_sign_mean_or_upper_bound():
    assert screen_retain(-0.1, -0.01, [-0.4, -0.2], "pressure_x") is True
    assert screen_retain(0.0, -0.01, [-0.4], "pressure_x") is False
    assert screen_retain(-0.1, 0.0, [-0.4], "pressure_x") is False
    assert screen_retain(-0.1, -0.01, [0.4], "pressure_x") is False
    assert screen_retain(-0.1, -0.01, [0.0], "ol_d") is False


def _margin_games(seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for season, n in ((2015, 30), (2016, 12)):
        for i in range(n):
            epa = float(rng.normal())
            week = (i % 6) + 1
            rows.append({
                "season": season,
                "week": week,
                "game_type": "REG",
                "kickoff": f"{season}-09-{week:02d} 13:00",
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
    assert screen["exploratory"] is True
    assert screen["ship_gate"] is False
    assert screen["structured_shift_sd"] == pytest.approx(flipped_screen["structured_shift_sd"])
    assert screen["endpoint"] == "margin"
    posted = games.copy()
    posted.loc[(posted["season"] == 2016) & (posted["week"] == 1), "game_type"] = "POST"
    with pytest.raises(DesignError):
        exploratory_screen(posted, "pressure_x")


def test_date_guards_use_eastern_time_and_require_game_type():
    bare = pd.DataFrame({"season": [2016], "week": [1], "kickoff": ["2016-09-11 13:00"]})
    with pytest.raises(DesignError):
        require_schedule_fields(bare)
    old = pd.DataFrame({
        "season": [2012], "week": [1], "kickoff": ["2012-09-09 13:00"], "game_type": ["REG"],
    })
    with pytest.raises(DesignError):
        require_schedule_fields(old)
    assert is_january_2027_kickoff("2027-01-03 13:00")
    assert is_january_2027_kickoff("2027-01-10 16:25")
    assert is_january_2027_kickoff(pd.Timestamp("2027-01-01 05:30", tz="UTC"))
    assert not is_january_2027_kickoff("2026-12-31 20:20")
    assert not is_january_2027_kickoff(pd.Timestamp("2027-01-01 03:00", tz="UTC"))
    assert not is_january_2027_kickoff("2027-02-14 18:30")
    assert not is_january_2027_kickoff("2027-09-12 13:00")


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
         "off_pressures": 10, "off_dropbacks": 40, "qb_att": 2.5,
         "qb_att_time": "2018-09-08 12:00", "kickoff": "2018-09-09 13:00"},
        {"season": 2018, "week": 1, "game_id": "w1a", "team": "BBB", "opponent": "AAA",
         "off_pressures": 8, "off_dropbacks": 40, "qb_att": 2.6,
         "qb_att_time": "2018-09-08 12:00", "kickoff": "2018-09-09 13:00"},
        {"season": 2018, "week": 2, "game_id": "w2a", "team": "AAA", "opponent": "BBB",
         "off_pressures": 20, "off_dropbacks": 40, "qb_att": 2.5,
         "qb_att_time": "2018-09-15 12:00", "kickoff": "2018-09-16 13:00"},
        {"season": 2018, "week": 2, "game_id": "w2a", "team": "BBB", "opponent": "AAA",
         "off_pressures": 4, "off_dropbacks": 40, "qb_att": 2.9,
         "qb_att_time": "2018-09-15 12:00", "kickoff": "2018-09-16 13:00"},
        {"season": 2018, "week": 2, "game_id": "w2b", "team": "CCC", "opponent": "DDD",
         "off_pressures": 20, "off_dropbacks": 40, "qb_att": 2.9,
         "qb_att_time": "2018-09-15 12:00", "kickoff": "2018-09-16 13:00"},
        {"season": 2018, "week": 4, "game_id": "w4a", "team": "AAA", "opponent": "BBB",
         "off_pressures": 40, "off_dropbacks": 40, "qb_att": 9.0,
         "qb_att_time": "2018-09-29 12:00", "kickoff": "2018-09-30 13:00"},
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
         "off_pressures": 4, "off_dropbacks": 20, "qb_att": 2.5,
         "qb_att_time": "2018-09-08 12:00", "kickoff": "2018-09-09 13:00"},
        {"season": 2018, "week": 2, "game_id": "b", "team": "AAA", "opponent": "BBB",
         "off_pressures": 10, "off_dropbacks": 20, "qb_att": 2.5,
         "qb_att_time": "2018-09-15 12:00", "kickoff": "2018-09-16 13:00"},
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


def _games(home_qb="QB-H", away_qb="QB-A", prev=True, asof="2026-10-11 10:00"):
    rows = []
    if prev:
        rows.append({
            "game_id": "prev", "season": 2026, "week": 4, "kickoff": "2026-10-04 17:00",
            "home": "HOM", "away": "AWY", "home_qb": home_qb, "away_qb": away_qb,
            "home_qb_asof": "2026-10-04 10:00", "away_qb_asof": "2026-10-04 10:00",
        })
    rows.append({
        "game_id": "cur", "season": 2026, "week": 5, "kickoff": "2026-10-11 17:00",
        "home": "HOM", "away": "AWY", "home_qb": home_qb, "away_qb": away_qb,
        "home_qb_asof": asof, "away_qb_asof": asof,
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
        "home_qb_asof": "2026-11-08 10:00", "away_qb_asof": "2026-11-08 10:00",
    }])
    feature = ol_matchup_feature(pd.DataFrame(snaps), injuries, games)
    assert feature["ol_d"].iloc[0] == pytest.approx(0.0)

    early = games.copy()
    early["week"] = 5
    early["game_id"] = "w5"
    early["kickoff"] = "2026-10-11 17:00"
    early["home_qb_asof"] = "2026-10-11 10:00"
    early["away_qb_asof"] = "2026-10-11 10:00"
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


def test_pressure_halts_on_season_grain_and_late_time_to_throw():
    weekly = pd.DataFrame([
        {"season": 2018, "week": 1, "game_id": "g", "team": "AAA", "opponent": "BBB",
         "off_pressures": 1, "off_dropbacks": 10, "qb_att": 2.5,
         "qb_att_time": "2018-09-08 12:00", "kickoff": "2018-09-09 13:00"},
        {"season": 2018, "week": 2, "game_id": "h", "team": "AAA", "opponent": "BBB",
         "off_pressures": 2, "off_dropbacks": 10, "qb_att": 2.5,
         "qb_att_time": "2018-09-15 12:00", "kickoff": "2018-09-16 13:00"},
    ])
    assert_pressure_not_season_grain(weekly)
    season_table = weekly.drop(columns=["game_id"])
    with pytest.raises(DesignError):
        assert_pressure_not_season_grain(season_table)
    late = weekly.copy()
    late["qb_att_time"] = "2018-09-09 18:00"
    late.loc[late["week"] == 2, "qb_att_time"] = "2018-09-16 18:00"
    games = pd.DataFrame([
        {"game_id": "w3", "season": 2018, "week": 3, "home": "AAA", "away": "BBB"},
    ])
    with pytest.raises(DesignError):
        pressure_matchup_feature(late, games)
    late_qb = _games(asof="2026-10-11 18:00")
    with pytest.raises(DesignError):
        ol_matchup_feature(
            pd.DataFrame(_five_starters("HOM", [1, 2, 3, 4]) + _five_starters("AWY", [1, 2, 3, 4])),
            pd.DataFrame(columns=["season", "week", "team", "player_id", "report_status", "report_time"]),
            late_qb,
        )


def _confirmatory_frame() -> pd.DataFrame:
    rng = np.random.default_rng(1)
    rows = []
    for season in (2015, 2016):
        for week in range(1, 8):
            for i in range(2):
                rows.append(_study_row(season, week, i, rng, kickoff=f"{season}-09-{week:02d} 13:00"))
    for week, kickoff in ((17, "2027-01-03 13:00"), (18, "2027-01-10 13:00")):
        for i in range(2):
            rows.append(_study_row(2026, week, i, rng, kickoff=kickoff))
    for week in (1, 2):
        for i in range(2):
            rows.append(_study_row(2027, week, i, rng, kickoff=f"2027-09-{week + 9:02d} 13:00"))
    return pd.DataFrame(rows)


def _study_row(season: int, week: int, i: int, rng: np.random.Generator, kickoff: str) -> dict:
    return {
        "season": season,
        "week": week,
        "game_type": "REG",
        "kickoff": kickoff,
        "spread_line": 0.0,
        "total_line": 44.0,
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


def test_stages_drop_screen_failures_and_guard_dates():
    games = _confirmatory_frame()
    screen = exploratory_screen(games, "pressure_x")
    assert screen["exploratory"] is True
    assert screen["ship_gate"] is False
    assert 2026 not in screen["seasons_graded"]
    assert 2016 in screen["seasons_graded"]
    harmed = harm_check(games, "pressure_x")
    assert harmed["benefit_claim"] is False
    assert harmed["expected_n"] == 43
    assert harmed["expected_clusters"] == 5
    assert set(harmed["graded"]["week"]) == {17, 18}
    no_january = games[games["season"] != 2026].copy()
    with pytest.raises(DesignError):
        harm_check(no_january, "pressure_x")
    confirm = confirmatory_look(games, ["pressure_x"])
    assert confirm["k"] == 1
    assert confirm["expected_n"] == 272
    assert confirm["expected_clusters"] == 18
    assert confirm["decisions"][0]["verdict"] in {"SHIP", "INCONCLUSIVE"}
    assert ATS_LB_LIMIT == pytest.approx(-0.02)
    assert HARM_UB_LIMIT == pytest.approx(0.25)
    bad_sign = games.copy()
    bad_sign["margin"] = 8.0 * bad_sign["pressure_x"]
    bad_sign["total"] = 8.0 * bad_sign["neutral_pace_sum"]
    dropped = exploratory_screen(bad_sign, "pressure_x")
    assert dropped["dropped"] is True
    stages = run_stages(bad_sign)
    assert "pressure_x" not in stages["retained"]
    assert "pressure_x" not in stages["harms"]
