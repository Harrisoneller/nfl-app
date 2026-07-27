"""Adjusted-EPA core: ridge opponent adjustment + EPA-driven predict_game."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.services import epa_adjust_service, predictions_service


# ---- Synthetic PBP ----------------------------------------------------------


def _synthetic_pbp(
    off_effects: dict[str, float],
    def_effects: dict[str, float],
    plays_per_game: int = 60,
    noise_sd: float = 0.0,
    seed: int = 7,
) -> pd.DataFrame:
    """Full round-robin: every team visits every other team once.

    Play EPA ~ off_i + def_j + noise, so ridge should recover the effects
    (shrunken toward 0).
    """
    rng = np.random.default_rng(seed)
    teams = sorted(off_effects)
    rows = []
    for home in teams:
        for away in teams:
            if home == away:
                continue
            gid = f"2025_{home}_{away}"
            for posteam, defteam in ((home, away), (away, home)):
                mu = off_effects[posteam] + def_effects[defteam]
                epa = mu + rng.normal(0, noise_sd, plays_per_game)
                for i, e in enumerate(epa):
                    rows.append({
                        "game_id": gid, "posteam": posteam, "defteam": defteam,
                        "home_team": home, "away_team": away,
                        "play_type": "pass" if i % 2 else "run",
                        "epa": e, "success": float(e > 0),
                        "wp": 0.5, "qtr": min(1 + i // 20, 3),
                        "drive": 1 + i // 6,
                        "game_seconds_remaining": 3600 - i * 28,
                        "cpoe": 2.0 if i % 2 else np.nan,
                        "pass_oe": 1.5,
                    })
    return pd.DataFrame(rows)


OFF = {"AAA": 0.15, "BBB": 0.05, "CCC": -0.05, "DDD": -0.15}
DEF = {"AAA": -0.05, "BBB": 0.05, "CCC": -0.02, "DDD": 0.02}


def test_ridge_recovers_effect_ordering_with_shrinkage():
    pbp = _synthetic_pbp(OFF, DEF)
    rows = epa_adjust_service.game_level_rows(pbp)
    off, deff = epa_adjust_service.ridge_adjust(rows, "epa", lam_games=6.0)

    order = sorted(off, key=off.get, reverse=True)
    assert order == ["AAA", "BBB", "CCC", "DDD"]
    # Shrinkage: estimates pulled toward 0, never past the true magnitude.
    assert 0 < off["AAA"] < 0.15 + 1e-9
    assert -0.15 - 1e-9 < off["DDD"] < 0
    # Defense recovered too (positive = leaky).
    assert deff["BBB"] > deff["AAA"]
    # Deviations are centered.
    assert abs(sum(off.values())) < 1e-9
    assert abs(sum(deff.values())) < 1e-9


def test_opponent_adjustment_discounts_soft_schedule():
    """Same raw production vs leaky defenses must adjust below the same raw
    production vs stout defenses."""
    pbp = _synthetic_pbp(OFF, DEF)
    rows = epa_adjust_service.game_level_rows(pbp)

    # BULY (bully) only plays the two leaky defenses; GRND only the two stout
    # ones. Both post identical raw +0.10 EPA/play.
    extra = []
    for tm, opps in (("BULY", ["BBB", "DDD"]), ("GRND", ["AAA", "CCC"])):
        for opp in opps:
            extra.append({
                "game_id": f"2025_{tm}_{opp}", "posteam": tm, "defteam": opp,
                "epa": 0.10, "success": 0.5, "plays": 60, "home_team": tm,
                "is_home": 1.0,
            })
    rows = pd.concat([rows, pd.DataFrame(extra)], ignore_index=True)
    off, _ = epa_adjust_service.ridge_adjust(rows, "epa", lam_games=2.0)
    assert off["GRND"] > off["BULY"]


def test_more_shrinkage_with_higher_lambda():
    pbp = _synthetic_pbp(OFF, DEF)
    rows = epa_adjust_service.game_level_rows(pbp)
    off_lo, _ = epa_adjust_service.ridge_adjust(rows, "epa", lam_games=1.0)
    off_hi, _ = epa_adjust_service.ridge_adjust(rows, "epa", lam_games=25.0)
    assert abs(off_hi["AAA"]) < abs(off_lo["AAA"])


def test_compute_adjusted_metrics_end_to_end_with_prior():
    pbp = _synthetic_pbp(OFF, DEF)
    prior = {"AAA": {k: 0.0 for k in epa_adjust_service.ADJUSTED_KEYS}}
    out = epa_adjust_service.compute_adjusted_metrics(pbp, prior=prior)
    assert set(OFF) <= set(out)
    row = out["AAA"]
    for k in epa_adjust_service.ADJUSTED_KEYS:
        assert k in row
    # Context metrics rode along.
    assert row["off_cpoe"] == pytest.approx(2.0)
    assert row["off_proe"] == pytest.approx(1.5)
    assert "off_neutral_sec_per_play" in row
    # AAA has 6 games (3 home + 3 away); a 0-valued prior at 3 pseudo-games
    # pulls the estimate toward 0 vs the no-prior fit.
    no_prior = epa_adjust_service.compute_adjusted_metrics(pbp)
    assert abs(row["adj_off_epa_per_play"]) < abs(no_prior["AAA"]["adj_off_epa_per_play"])


# ---- predict_game: EPA fundamentals path ------------------------------------


def _aggs(off=0.0, deff=0.0, sr_off=0.0, sr_def=0.0, cpoe=None, proe=None,
          pace=None, **extra):
    d = {
        "adj_off_epa_per_play": off, "adj_def_epa_per_play": deff,
        "adj_off_success_rate": sr_off, "adj_def_success_rate": sr_def,
        "points_per_game": 22.0, "points_allowed_per_game": 22.0,
    }
    if cpoe is not None:
        d["off_cpoe"] = cpoe
    if proe is not None:
        d["off_proe"] = proe
    if pace is not None:
        d["off_neutral_sec_per_play"] = pace
    d.update(extra)
    return d


def test_predict_game_epa_path_beats_fallback_flag():
    pred = predictions_service.predict_game(
        1500, 1500, home_aggs=_aggs(off=0.10), away_aggs=_aggs(off=-0.10),
    )
    assert pred["inputs"]["fundamentals"] is not None
    assert pred["explainability"]["method"] == "adjusted_epa_v2"
    # Better offense (plus HFA) → home favored, higher expected points.
    assert pred["home_win_prob"] > 0.5
    assert pred["inputs"]["expected_home_pts"] > pred["inputs"]["expected_away_pts"]


def test_predict_game_falls_back_to_ppg_without_adjusted_metrics():
    pred = predictions_service.predict_game(
        1550, 1450, home_off_ppg=27.0, away_off_ppg=17.0,
        home_def_ppg_allowed=20.0, away_def_ppg_allowed=24.0,
    )
    assert pred["inputs"]["fundamentals"] is None
    assert pred["explainability"]["method"] == "heuristic_inputs_v1"
    assert pred["home_win_prob"] > 0.5


def test_better_adjusted_offense_monotonic_in_margin():
    margins = []
    for off in (-0.10, 0.0, 0.10):
        pred = predictions_service.predict_game(
            1500, 1500, home_aggs=_aggs(off=off), away_aggs=_aggs(),
        )
        margins.append(pred["distribution"]["expected_margin"])
    assert margins[0] < margins[1] < margins[2]


def test_leaky_defense_raises_opponent_points():
    tight = predictions_service.predict_game(
        1500, 1500, home_aggs=_aggs(), away_aggs=_aggs(deff=-0.08),
    )
    leaky = predictions_service.predict_game(
        1500, 1500, home_aggs=_aggs(), away_aggs=_aggs(deff=0.08),
    )
    assert leaky["inputs"]["expected_home_pts"] > tight["inputs"]["expected_home_pts"]


def test_fast_pace_and_proe_raise_total():
    base = predictions_service.predict_game(
        1500, 1500, home_aggs=_aggs(pace=27.0), away_aggs=_aggs(pace=27.0),
    )
    fast = predictions_service.predict_game(
        1500, 1500, home_aggs=_aggs(pace=24.0), away_aggs=_aggs(pace=24.0),
    )
    passy = predictions_service.predict_game(
        1500, 1500,
        home_aggs=_aggs(pace=27.0, proe=6.0), away_aggs=_aggs(pace=27.0, proe=6.0),
    )
    assert fast["predicted_total"] > base["predicted_total"]
    assert passy["predicted_total"] > base["predicted_total"]


def test_w_fundamentals_zero_reduces_to_pure_elo():
    from app.services import param_registry

    with param_registry.overlay({"game.w_fundamentals": 0.0}):
        with_fund = predictions_service.predict_game(
            1550, 1450, home_aggs=_aggs(off=0.10), away_aggs=_aggs(off=-0.10),
        )
        elo_only = predictions_service.predict_game(1550, 1450)
    assert with_fund["predicted_spread"] == elo_only["predicted_spread"]
    assert with_fund["home_win_prob"] == elo_only["home_win_prob"]


def test_cpoe_credit_moves_margin():
    base = predictions_service.predict_game(
        1500, 1500, home_aggs=_aggs(), away_aggs=_aggs(),
    )
    accurate_qb = predictions_service.predict_game(
        1500, 1500, home_aggs=_aggs(cpoe=5.0), away_aggs=_aggs(),
    )
    assert (
        accurate_qb["distribution"]["expected_margin"]
        > base["distribution"]["expected_margin"]
    )


def test_admin_lever_passthrough_scales_epa_expected_points():
    boosted = _aggs(off=0.05)
    boosted["_input_adjustment"] = {"points_per_game": {"from": 22.0, "to": 26.4}}
    base = predictions_service.predict_game(
        1500, 1500, home_aggs=_aggs(off=0.05), away_aggs=_aggs(),
    )
    levered = predictions_service.predict_game(
        1500, 1500, home_aggs=boosted, away_aggs=_aggs(),
    )
    assert levered["inputs"]["expected_home_pts"] == pytest.approx(
        base["inputs"]["expected_home_pts"] * 1.2, rel=0.02,
    )
