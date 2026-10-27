"""Hand-checkable fixtures and two pinned 2023 team-seasons for features.nfl.defs."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from features.nfl.defs import (
    BASE_INT_RATE,
    BASE_RZ_TD,
    CONTRACT,
    DEF_WEIGHT,
    FUMBLE_EXPECTED,
    K_EPA,
    K_FG,
    K_FUMBLE,
    K_INT,
    K_ONE_SCORE,
    K_RZ,
    ONE_SCORE_EXPECTED,
    PBP_INPUT_COLUMNS,
    REQUIRED,
    RIDGE_EPS,
    TEAM_GAME_COLUMNS,
    TEAM_SEASON_COLUMNS,
    assert_no_market_columns,
    assert_output_columns,
    build_features,
    field_goal_expected_make,
    ridge_team,
    shrink,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "pbp_2023_reg_slim.parquet"
# play_by_play_2023.parquet from the nflverse pbp release pinned at
# updated_at 2026-10-26T16:29:22Z, sha256 bd3484731408def6b0ec93225bba2bd7b2c65769ca707a2b9444d891abdc6776.
# The fixture is that file's 2023 regular-season rows, market columns removed,
# with fumble_forced kept so recovery luck runs on the production path.

# Frozen outputs of build_features on that fixture. Rounded checks use 1e-6.
GOLDEN = {
    ("2023", "DET"): {
        "games": 17,
        "n_plays_season": 819,
        "epa_play_season": 0.08186294127352693,
        "success_rate_season": 0.46886446886446886,
        "explosive_rate_season": 0.08058608058608059,
        "pass_epa_season": 0.16295416376405245,
        "rush_epa_season": -0.02954934701780378,
        "early_epa_season": 0.059047765167043235,
        "passing_epa_season": 0.07604111462801491,
        "sched_strength": 0.092926292891514,
        "fumble_n": 24,
        "fumble_rec_luck": -0.06756756756756757,
        "int_luck": -0.004152519646744936,
        "rz_td_luck": 0.04596477089569418,
        "one_score_luck": 0.041666666666666664,
        "fg_luck": 0.00813077877286814,
    },
    ("2023", "KC"): {
        "games": 17,
        "n_plays_season": 907,
        "epa_play_season": 0.03391551528323485,
        "success_rate_season": 0.4421168687982359,
        "explosive_rate_season": 0.06063947078280044,
        "pass_epa_season": 0.08691201347840613,
        "rush_epa_season": -0.08115730072795871,
        "early_epa_season": -0.021581811579974363,
        "passing_epa_season": -0.01926270529630443,
        "sched_strength": 0.0716353845700137,
        "fumble_n": 30,
        "fumble_rec_luck": -0.03749999999999999,
        "int_luck": -0.004830291178792844,
        "rz_td_luck": -0.01495395499176683,
        "one_score_luck": 0.0185185185185185,
        "fg_luck": 0.04977231755692952,
    },
}


def _play(**kwargs) -> dict:
    base = {
        "game_id": "G1", "season": 2020, "week": 1,
        "posteam": "KC", "defteam": "DEN", "home_team": "KC", "away_team": "DEN",
        "epa": 0.0, "success": 0, "pass": 1, "rush": 0,
        "down": 1, "ydstogo": 10, "yards_gained": 4, "wp": 0.50,
        "qb_kneel": 0, "qb_spike": 0, "special_teams_play": 0,
        "home_score": 21, "away_score": 17,
        "interception": 0, "fumble": 0, "fumble_recovery_1_team": None,
        "pass_attempt": 1, "pass_defense_1_player_id": None,
        "field_goal_attempt": 0, "field_goal_result": None, "kick_distance": None,
        "yardline_100": 50, "touchdown": 0, "drive": 1, "fixed_drive": 1,
    }
    base.update(kwargs)
    return base


def _hand_plays() -> pd.DataFrame:
    """Two games. Counts below are the plays that survive the WP band."""
    return pd.DataFrame([
        # Kept. Early-down pass, success, fumble recovered by KC, red-zone TD.
        _play(epa=1.0, success=1, yards_gained=6, fumble=1, fumble_recovery_1_team="KC",
              yardline_100=10, touchdown=1, drive=1),
        # Kept. Explosive, not a success, interception on a defended pass.
        _play(epa=3.0, success=0, yards_gained=25, interception=1, pass_defense_1_player_id="00-0036355"),
        _play(epa=100.0, wp=0.05),
        _play(epa=100.0, wp=np.nan),
        _play(epa=100.0, qb_kneel=1),
        _play(epa=100.0, qb_spike=1),
        _play(epa=100.0, special_teams_play=1),
        # 3rd-and-4 is neither family. DEN rush, EPA 9.
        _play(epa=9.0, success=1, posteam="DEN", defteam="KC", **{"pass": 0}, rush=1,
              pass_attempt=0, down=3, ydstogo=4, yards_gained=3),
        # Field goal. Special teams, so it stays out of EPA. Made from 40.
        _play(special_teams_play=1, field_goal_attempt=1, field_goal_result="made",
              kick_distance=40, epa=np.nan, pass_attempt=0),
        # Week 2. One KC pass, EPA 2. One DEN rush, EPA -1.
        _play(game_id="G2", week=2, epa=2.0, success=1, yards_gained=8),
        _play(game_id="G2", week=2, epa=-1.0, success=0, posteam="DEN", defteam="KC",
              **{"pass": 0}, rush=1, pass_attempt=0, yards_gained=2),
    ])


# Names a spread/odds/moneyline token list misses, plus the lines it already
# catches. ``*_odds`` and ``*moneyline`` stand in for any such column.
_MARKET_NAMES = (
    "vegas_wp",
    "vegas_home_wp",
    "spread_line",
    "total_line",
    "total",
    "over_odds",
    "home_spread_odds",
    "team_odds",
    "home_moneyline",
    "away_moneyline",
    "moneyline",
    "foo_moneyline",
    "closing_spread",
    "over_under",
    "spread",
)


def test_score_totals_and_model_wp_are_not_market_columns():
    assert_no_market_columns(["epa", "wp", "total_home_score", "total_away_score", "yardline_100", "play_id"])
    game, season = build_features(_hand_plays())
    assert_output_columns(game.columns, TEAM_GAME_COLUMNS)
    assert_output_columns(season.columns, TEAM_SEASON_COLUMNS)


@pytest.mark.parametrize("name", _MARKET_NAMES)
def test_market_like_column_rejected_on_input_and_both_outputs(name: str):
    with pytest.raises(ValueError, match="market columns"):
        assert_no_market_columns(["epa", "wp", name])
    plays = _hand_plays()
    plays[name] = 1.0
    with pytest.raises(ValueError, match="market columns"):
        build_features(plays)
    with pytest.raises(ValueError, match="market columns"):
        assert_output_columns([*TEAM_GAME_COLUMNS, name], TEAM_GAME_COLUMNS)
    with pytest.raises(ValueError, match="market columns"):
        assert_output_columns([*TEAM_SEASON_COLUMNS, name], TEAM_SEASON_COLUMNS)


def test_pbp_allowlist_is_the_columns_defs_reads():
    assert set(REQUIRED) <= set(PBP_INPUT_COLUMNS)
    assert "fumble_forced" in PBP_INPUT_COLUMNS
    assert "fumble_lost" not in PBP_INPUT_COLUMNS
    for name in ("vegas_wp", "vegas_home_wp", "spread_line", "total_line", "home_moneyline", "over_odds"):
        assert name not in PBP_INPUT_COLUMNS
    if not FIXTURE.exists():
        pytest.fail(f"missing pinned fixture {FIXTURE}")
    plays = pd.read_parquet(FIXTURE)
    assert "fumble_forced" in plays.columns
    assert set(plays.columns) <= set(PBP_INPUT_COLUMNS)


def test_columns_off_the_read_list_never_reach_outputs():
    """A pure input allowlist. These names are not market tokens and not reads."""
    banned = ("cover", "ats_margin", "implied_prob", "no_vig_prob")
    plays = _hand_plays()
    for name in banned:
        plays[name] = 1.0
    game, season = build_features(plays)
    clean_game, clean_season = build_features(_hand_plays())
    for name in banned:
        assert name not in game.columns
        assert name not in season.columns
        assert name not in PBP_INPUT_COLUMNS
    assert game.equals(clean_game)
    assert season.equals(clean_season)


def test_recovery_luck_counts_every_fumble_and_ignores_lost():
    """Recovery is who falls on the ball, so unforced fumbles count too.

    ``fumble_lost`` does not. A forced fumble the offense keeps is still a fumble.
    Forced fumbles are used only in havoc elsewhere, and this frame has no havoc column.
    """
    plays = pd.DataFrame([
        _play(epa=1.0, fumble=1, fumble_forced=1, fumble_lost=0, fumble_recovery_1_team="KC"),
        _play(epa=0.2, fumble=1, fumble_forced=0, fumble_lost=0, fumble_recovery_1_team="KC"),
        _play(epa=0.3, fumble=0, fumble_forced=0, fumble_lost=1, fumble_recovery_1_team="DEN"),
    ])
    _, season = build_features(plays)
    kc = season[season["team"] == "KC"].iloc[0]
    den = season[season["team"] == "DEN"].iloc[0]
    assert int(plays["fumble_forced"].sum()) == 1
    assert kc["fumble_n"] == 2
    assert den["fumble_n"] == 2
    assert kc["fumble_rec_luck"] == pytest.approx(shrink(1.0 - FUMBLE_EXPECTED, 2, K_FUMBLE))
    assert den["fumble_rec_luck"] == pytest.approx(shrink(0.0 - FUMBLE_EXPECTED, 2, K_FUMBLE))
    assert "every fumble" in CONTRACT["fumble_n"][2].lower()
    assert "forced fumbles are used only in havoc elsewhere" in CONTRACT["fumble_n"][2].lower()
    assert "forced fumbles are used only in havoc elsewhere" in CONTRACT["fumble_rec_luck"][2].lower()
    assert "fumble_lost" in CONTRACT["fumble_rec_luck"][2]
    assert "havoc" not in season.columns
    assert "havoc" not in set(CONTRACT)


def test_garbage_band_families_and_separate_explosiveness():
    game, season = build_features(_hand_plays())
    kc_w2 = game[(game["team"] == "KC") & (game["week"] == 2)].iloc[0]
    den_w2 = game[(game["team"] == "DEN") & (game["week"] == 2)].iloc[0]
    # Week-1 kept plays: KC epa 1 and 3. The 0.05, missing WP, kneel, spike, and special-teams rows are out.
    assert kc_w2["n_plays_season"] == 2
    assert kc_w2["epa_play_season"] == pytest.approx(2.0)
    assert kc_w2["success_rate_season"] == pytest.approx(0.5)
    assert kc_w2["explosive_rate_season"] == pytest.approx(0.5)
    assert kc_w2["early_epa_season"] == pytest.approx(2.0)
    assert pd.isna(kc_w2["passing_epa_season"])
    # DEN's only prior play is 3rd-and-4, which is neither family.
    assert den_w2["epa_play_season"] == pytest.approx(9.0)
    assert pd.isna(den_w2["early_epa_season"])
    assert pd.isna(den_w2["passing_epa_season"])
    # EPA allowed is the opponent's offense, not a copy of your own.
    assert kc_w2["epa_allowed_season"] == pytest.approx(9.0)
    assert den_w2["epa_allowed_season"] == pytest.approx(2.0)
    # Last four prior games is that one game.
    assert kc_w2["epa_play_last4"] == pytest.approx(2.0)
    assert kc_w2["n_plays_last4"] == 2
    # Two-team midrank. DEN 9 > KC 2, so 75 and 25.
    assert den_w2["epa_play_season_pct"] == pytest.approx(75.0)
    assert kc_w2["epa_play_season_pct"] == pytest.approx(25.0)

    kc = season[season["team"] == "KC"].iloc[0]
    den = season[season["team"] == "DEN"].iloc[0]
    assert kc["games"] == 2
    assert kc["n_plays_season"] == 3
    assert kc["epa_play_season"] == pytest.approx(2.0)
    assert kc["explosive_rate_season"] == pytest.approx(1.0 / 3.0)
    assert kc["success_rate_season"] == pytest.approx(2.0 / 3.0)
    assert den["epa_play_season"] == pytest.approx(4.0)


def test_down_family_cuts():
    plays = pd.DataFrame([
        _play(down=2, ydstogo=7, epa=1.0),
        _play(down=2, ydstogo=8, epa=2.0),
        _play(down=3, ydstogo=4, epa=3.0),
        _play(down=4, ydstogo=5, epa=4.0),
        _play(wp=0.10, epa=5.0, down=1),
        _play(wp=0.90, epa=6.0, down=1),
        _play(wp=0.099, epa=100.0),
        _play(wp=0.901, epa=100.0),
    ])
    _, season = build_features(plays)
    kc = season[season["team"] == "KC"].iloc[0]
    # 0.10 and 0.90 stay. 0.099 and 0.901 go. Six plays, mean epa (1+2+3+4+5+6)/6.
    assert kc["n_plays_season"] == 6
    assert kc["epa_play_season"] == pytest.approx(21.0 / 6.0)
    # Early: 2nd-and-7 plus the two first downs at the band edges. Passing: 2nd-and-8 and 4th-and-5.
    assert kc["early_epa_season"] == pytest.approx((1.0 + 5.0 + 6.0) / 3.0)
    assert kc["passing_epa_season"] == pytest.approx((2.0 + 4.0) / 2.0)


def test_luck_shrinkage_by_hand():
    _, season = build_features(_hand_plays())
    game, _ = build_features(_hand_plays())
    kc = season[season["team"] == "KC"].iloc[0]
    den = season[season["team"] == "DEN"].iloc[0]
    assert shrink(0.5, 1, K_FUMBLE) == pytest.approx(0.5 / 51.0)
    assert kc["fumble_n"] == 1
    assert kc["fumble_rec_luck"] == pytest.approx(shrink(1.0 - FUMBLE_EXPECTED, 1, K_FUMBLE))
    assert den["fumble_rec_luck"] == pytest.approx(shrink(0.0 - FUMBLE_EXPECTED, 1, K_FUMBLE))

    # One defended interception in two prior attempts, and the leave-one-out league is empty.
    kc_w2 = game[(game["team"] == "KC") & (game["week"] == 2)].iloc[0]
    residual = BASE_INT_RATE - 0.5
    assert kc_w2["int_n"] == 2
    assert kc_w2["int_luck"] == pytest.approx(shrink(residual, 2, K_INT))

    # One red-zone trip, one TD, empty leave-one-out league, baseline 0.55.
    assert kc["rz_n"] == 1
    assert kc["rz_td_luck"] == pytest.approx(shrink(1.0 - BASE_RZ_TD, 1, K_RZ))

    # Both games finish 21-17. KC wins both. Shrinkage uses k = 16 games.
    assert kc["one_score_n"] == 2
    assert kc["one_score_luck"] == pytest.approx(shrink(1.0 - ONE_SCORE_EXPECTED, 2, K_ONE_SCORE))
    assert den["one_score_luck"] == pytest.approx(shrink(0.0 - ONE_SCORE_EXPECTED, 2, K_ONE_SCORE))

    made = 1.0 - field_goal_expected_make(40)
    assert field_goal_expected_make(40) == pytest.approx(1.0 / (1.0 + np.exp(-1.8)))
    assert kc["fg_n"] == 1
    assert kc["fg_luck"] == pytest.approx(shrink(made, 1, K_FG))
    assert shrink(1.0, 0, K_FG) == 0.0


def test_ridge_matches_an_explicit_normal_equation():
    # Week-1 rates: KC (home) 2.0 EPA/play, DEN (away) 9.0. Teams sort as DEN, KC.
    y = np.array([2.0, 9.0])
    x = np.array([
        [0.0, 1.0, 1.0, 0.0, 1.0, 1.0],  # off KC + def DEN + ic + hfa
        [1.0, 0.0, 0.0, 1.0, 1.0, 0.0],  # off DEN + def KC + ic
    ])
    pen = np.array([K_EPA, K_EPA, K_EPA, K_EPA, RIDGE_EPS, RIDGE_EPS])
    a = x.T @ x
    a.flat[::7] += pen
    rhs = x.T @ y
    coef = np.linalg.solve(a, rhs)
    off, de, ic, hfa = ridge_team(
        np.array([1, 0]),
        np.array([0, 1]),
        np.array([1.0, 0.0]),
        y,
        np.ones(2),
        2,
        np.zeros(2),
        np.zeros(2),
        K_EPA,
    )
    assert off[1] == pytest.approx(coef[1])
    assert de[0] == pytest.approx(coef[2])
    assert ic == pytest.approx(coef[4])
    assert hfa == pytest.approx(coef[5])

    game, _ = build_features(_hand_plays())
    kc = game[(game["team"] == "KC") & (game["week"] == 2)].iloc[0]
    den = game[(game["team"] == "DEN") & (game["week"] == 2)].iloc[0]
    assert kc["epa_off_adj"] == pytest.approx(off[1])
    assert den["epa_off_adj"] == pytest.approx(off[0])
    assert kc["sched_strength"] == pytest.approx(off[1] - DEF_WEIGHT * de[1])
    week1 = game[game["week"] == 1]
    assert week1["sched_strength"].eq(0.0).all()


def test_franchise_alias_and_empty_frame():
    plays = pd.DataFrame([_play(posteam="LA", defteam="SD", home_team="LA", away_team="SD")])
    _, season = build_features(plays)
    assert set(season["team"]) == {"LAR", "LAC"}
    empty = pd.DataFrame(columns=list(_play().keys()))
    game, season = build_features(empty)
    assert list(game.columns) == list(TEAM_GAME_COLUMNS)
    assert list(season.columns) == list(TEAM_SEASON_COLUMNS)
    assert len(game) == 0 and len(season) == 0


def test_contract_covers_every_output_column():
    game, season = build_features(_hand_plays())
    assert set(game.columns) <= set(CONTRACT)
    assert set(season.columns) <= set(CONTRACT)
    assert list(game.columns) == list(TEAM_GAME_COLUMNS)
    assert list(season.columns) == list(TEAM_SEASON_COLUMNS)
    for col, spec in CONTRACT.items():
        assert len(spec) == 3
        dtype, unit, desc = spec
        assert dtype in {int, float, str}
        assert unit and desc
        frame = game if col in game.columns else season
        if len(frame) == 0 or col not in frame.columns:
            continue
        if dtype is float:
            assert pd.api.types.is_float_dtype(frame[col])
        elif dtype is int:
            assert pd.api.types.is_integer_dtype(frame[col])
    assert_no_market_columns(game.columns)
    assert_no_market_columns(season.columns)


def test_golden_2023_team_seasons():
    if not FIXTURE.exists():
        pytest.fail(f"missing pinned fixture {FIXTURE}")
    plays = pd.read_parquet(FIXTURE)
    assert_no_market_columns(plays.columns)
    _, season = build_features(plays)
    for (year, team), expect in GOLDEN.items():
        row = season[(season["season"] == int(year)) & (season["team"] == team)]
        assert len(row) == 1
        got = row.iloc[0]
        assert int(got["games"]) == expect["games"]
        for key, value in expect.items():
            if key == "games":
                continue
            assert got[key] == pytest.approx(value, abs=1e-6, rel=1e-6), key
