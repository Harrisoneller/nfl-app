"""v5 game model, opportunity player model, and the data-gating fixes.

All DB-free and network-free: synthetic PBP / weekly frames only.
"""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
import pytest

from app.adapters.data import nflverse_stats
from app.services import game_model_v5_service as v5s
from app.services import player_opportunity_model as om
from app.services import predictions_service as ps
from app.services import team_ratings_v5 as v5

TEAMS = ["AAA", "BBB", "CCC", "DDD"]
# True per-play quality: offense (+ good), defense (+ = allows more = bad).
TRUE_OFF = {"AAA": 0.15, "BBB": 0.05, "CCC": -0.05, "DDD": -0.15}
TRUE_DEF = {"AAA": -0.10, "BBB": 0.0, "CCC": 0.0, "DDD": 0.10}


def _synthetic_pbp(season: int, weeks: int, seed: int = 0, qb_for=None) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    pairs = [(h, a) for h in TEAMS for a in TEAMS if h != a]
    for w in range(1, weeks + 1):
        for gi, (h, a) in enumerate(pairs[(w * 2) % len(pairs):][:2] or pairs[:2]):
            gid = f"{season}_{w:02d}_{a}_{h}"
            for off, de in ((h, a), (a, h)):
                for i in range(60):
                    is_pass = i % 2 == 0
                    epa = TRUE_OFF[off] + TRUE_DEF[de] + rng.normal(0, 1.0)
                    qb = (qb_for or {}).get((off, w), f"QB-{off}")
                    rows.append({
                        "game_id": gid, "week": w, "posteam": off, "defteam": de,
                        "home_team": h, "epa": epa, "success": float(epa > 0),
                        "pass": float(is_pass), "rush": float(not is_pass),
                        "qb_kneel": 0.0, "qb_spike": 0.0, "wp": 0.5, "qtr": 2,
                        "score_differential": 0, "special_teams_play": 0.0,
                        "qb_dropback": float(is_pass), "passer_player_id": qb if is_pass else None,
                    })
    return pd.DataFrame(rows)


def _schedule(pbp: pd.DataFrame, season: int) -> pd.DataFrame:
    g = pbp.groupby("game_id").agg(week=("week", "first"), home_team=("home_team", "first")).reset_index()
    g["away_team"] = g["game_id"].str.split("_").str[2]
    g["season"] = season
    g["home_score"] = 24
    g["away_score"] = 20
    g["spread_line"] = 3.0
    g["location"] = "Home"
    return g


# ---- team_game_rows ------------------------------------------------------------


def test_team_game_rows_filters_kneels_and_weights_garbage_time():
    pbp = _synthetic_pbp(2025, 1)
    gid = pbp["game_id"].iloc[0]
    extra = pbp[pbp["game_id"] == gid].iloc[:2].copy()
    extra["qb_kneel"] = [1.0, 0.0]
    extra["wp"] = [0.5, 0.99]   # second snap is garbage time
    extra["epa"] = [-50.0, 50.0]  # absurd values: kneel must vanish, outlier must clip
    rows = v5.team_game_rows(pd.concat([pbp, extra]), 2025)
    base = v5.team_game_rows(pbp, 2025)
    team = extra["posteam"].iloc[0]
    r = rows[(rows.game_id == gid) & (rows.posteam == team)].iloc[0]
    b = base[(base.game_id == gid) & (base.posteam == team)].iloc[0]
    assert r["n"] == b["n"] + 1                     # kneel dropped
    assert r["gw"] == pytest.approx(b["gw"] + 0.35)  # garbage snap down-weighted
    assert r["w_epa"] - b["w_epa"] == pytest.approx(0.35 * 4.5)  # winsorized at 4.5
    assert set(rows["qb"].dropna()) == {f"QB-{t}" for t in set(rows["posteam"])}


def test_fit_ratings_recovers_offense_and_defense_order():
    pbp = _synthetic_pbp(2025, 12, seed=1)
    rows = v5.attach_points(v5.team_game_rows(pbp, 2025), _schedule(pbp, 2025))
    r = v5.fit_ratings(rows, 2025, 13, TEAMS)
    off, de, _, _ = r["epa"]
    assert off["AAA"] > off["BBB"] > off["CCC"] > off["DDD"]
    assert de["AAA"] < de["DDD"]
    assert abs(sum(off.values())) < 1e-9  # centered


def test_fit_ratings_uses_only_games_before_the_week():
    pbp = _synthetic_pbp(2025, 8, seed=2)
    rows = v5.attach_points(v5.team_game_rows(pbp, 2025), _schedule(pbp, 2025))
    a = v5.fit_ratings(rows, 2025, 5, TEAMS)
    mutated = rows.copy()
    mutated.loc[mutated.week >= 5, "w_epa"] += 999.0  # future games: must not matter
    b = v5.fit_ratings(mutated, 2025, 5, TEAMS)
    assert a["epa"][0] == pytest.approx(b["epa"][0])


# ---- QB layer ------------------------------------------------------------------


def test_qb_delta_zero_for_incumbent_and_negative_for_unknown_backup():
    pbp = _synthetic_pbp(2025, 10, seed=3)
    rows = v5.attach_points(v5.team_game_rows(pbp, 2025), _schedule(pbp, 2025))
    book = v5.build_qb_book(rows, 2025, 11, TEAMS)
    assert book.delta("AAA", "QB-AAA") == pytest.approx(0.0, abs=1e-9)
    # A QB the model has never seen sits at replacement: a big downgrade for AAA.
    assert book.delta("AAA", "NEVER-SEEN") < -0.1
    # None → falls back to the team's last starter → no change.
    assert book.delta("AAA", None) == pytest.approx(0.0, abs=1e-9)


def test_qb_prior_slides_with_volume():
    # A QB with a big sample of average play is rated near average; one with a
    # tiny sample of the same play is pulled toward replacement.
    pbp_big = _synthetic_pbp(2025, 12, seed=4, qb_for={("BBB", w): "VET" for w in range(1, 13)})
    pbp_small = _synthetic_pbp(2025, 12, seed=4, qb_for={("BBB", 12): "ROOK"})
    rb = v5.attach_points(v5.team_game_rows(pbp_big, 2025), _schedule(pbp_big, 2025))
    rs = v5.attach_points(v5.team_game_rows(pbp_small, 2025), _schedule(pbp_small, 2025))
    vet = v5.build_qb_book(rb, 2025, 13, TEAMS).rating("VET")
    rook = v5.build_qb_book(rs, 2025, 13, TEAMS).rating("ROOK")
    assert rook < vet


# ---- market prior --------------------------------------------------------------


def test_market_ratings_never_read_the_target_week_line():
    pbp = _synthetic_pbp(2025, 14, seed=5)
    sched = _schedule(pbp, 2025)
    sched["spread_line"] = np.where(sched.home_team == "AAA", 7.0, -1.0)
    a = v5.market_ratings(sched, 2025, 11, TEAMS)
    s2 = sched.copy()
    s2.loc[s2.week >= 11, "spread_line"] = 99.0
    b = v5.market_ratings(s2, 2025, 11, TEAMS)
    assert a is not None and a[0] == pytest.approx(b[0])


# ---- WeekModel -----------------------------------------------------------------


def _week_model(weeks: int = 10):
    pbp = _synthetic_pbp(2025, weeks, seed=6)
    sched = _schedule(pbp, 2025)
    rows = v5.attach_points(v5.team_game_rows(pbp, 2025), sched)
    return v5.build_week_model(rows, sched, 2025, weeks + 1, TEAMS)


def test_predict_margin_is_sum_of_parts_and_neutral_drops_hfa():
    wm = _week_model()
    p = wm.predict("AAA", "DDD")
    assert p["margin"] == pytest.approx(sum(p["margin_parts"].values()), abs=0.05)
    assert p["margin"] > 0  # best team at home vs worst
    n = wm.predict("AAA", "DDD", neutral=True)
    assert p["margin"] - n["margin"] == pytest.approx(wm.margin_coefs["hfa"], abs=1e-6)
    # Symmetry: swapping sides (neutral) flips the margin.
    assert wm.predict("DDD", "AAA", neutral=True)["margin"] == pytest.approx(-n["margin"], abs=1e-6)


def test_qb_change_moves_spread_by_coef_times_delta():
    wm = _week_model()
    base = wm.predict("AAA", "DDD")
    hurt = wm.predict("AAA", "DDD", home_qb="NEVER-SEEN")
    d = wm.qb.delta("AAA", "NEVER-SEEN")
    assert hurt["margin"] - base["margin"] == pytest.approx(wm.margin_coefs["qb_d"] * d, abs=1e-6)
    assert hurt["qb_change"]["home"] is True and base["qb_change"]["home"] is False


# ---- predict_game integration ---------------------------------------------------


def _v5_payload(margin=6.0, total=44.0, home_change=False):
    return {
        "model_version": v5.MODEL_VERSION, "margin": margin, "total": total,
        "margin_parts": {"epa_d": margin - 1.8, "hfa": 1.8}, "total_parts": {},
        "features": {"qb_h": 0.0, "qb_a": 0.0}, "uses_market_prior": False,
        "qb_change": {"home": home_change, "away": False},
    }


def test_predict_game_uses_v5_margin_and_total():
    p = ps.predict_game(1500, 1500, v5=_v5_payload())
    assert p["predicted_spread"] == pytest.approx(-6.0)
    assert p["predicted_total"] == pytest.approx(44.0)
    assert p["model_version"] == ps.PREDICTION_MODEL_VERSION
    assert p["inputs"]["v5"]["margin"] == pytest.approx(6.0)
    assert p["explainability"]["method"] == "qb_ridge_v5"


def test_predict_game_without_v5_is_legacy():
    p = ps.predict_game(1600, 1500)
    assert p["model_version"] == ps.LEGACY_MODEL_VERSION
    assert p["inputs"]["v5"] is None


def test_v5_qb_change_strips_context_qb_component_only():
    ctx = {
        "applicable": True, "points": -3.0, "points_uncapped": -3.0, "sigma_mult": 1.05,
        "confidence": 0.9, "capped": False,
        "home": {"points": -4.0, "sigma_mult": 1.05, "components": [
            {"component": "qb", "points": -3.5},
            {"component": "availability", "points": -0.5},
        ]},
        "away": {"points": -1.0, "sigma_mult": 1.0, "components": [
            {"component": "availability", "points": -1.0},
        ]},
        "matchup": None,
    }
    out = ps._strip_v5_qb_context(ctx, _v5_payload(home_change=True))
    assert out["points"] == pytest.approx(0.5)  # -3.0 + 3.5
    assert [c["component"] for c in out["home"]["components"]] == ["availability"]
    # No change flagged → context untouched (injury feed knows something v5 doesn't).
    assert ps._strip_v5_qb_context(ctx, _v5_payload(home_change=False)) is ctx


def test_v5_service_disabled_switch(monkeypatch):
    monkeypatch.setattr(v5s, "_p", lambda key, default: 0.0 if key == "game.v5_enabled" else default)
    assert asyncio.run(v5s.week_model(2026, 4)) is None


# ---- opportunity model ---------------------------------------------------------


@pytest.mark.parametrize("stat,hat", [("receiving_yards", 60.0), ("rushing_yards", 70.0),
                                      ("passing_yards", 240.0), ("receptions", 5.0)])
def test_quantiles_monotone_and_over_prob_consistent(stat, hat):
    q = om.quantiles(stat, hat)
    vals = list(q.values())
    assert vals == sorted(vals)
    assert om.over_prob(q, q[0.5]) == pytest.approx(0.5, abs=1e-9)
    lines = np.linspace(0, vals[-1] * 1.5, 40)
    probs = [om.over_prob(q, x) for x in lines]
    assert all(a >= b - 1e-12 for a, b in zip(probs, probs[1:]))
    assert 0.0 <= min(probs) and max(probs) <= 1.0


def test_yardage_median_below_mean():
    for stat in ("receiving_yards", "rushing_yards"):
        q = om.quantiles(stat, 60.0)
        assert q[0.5] < om.mean_from_quantiles(q)


def _weekly(n_games=6):
    rows = []
    for w in range(1, n_games + 1):
        for pid, pos, tg, ry in (("WR1", "WR", 9, 90), ("WR2", "WR", 4, 35), ("RB1", "RB", 3, 20)):
            rows.append({"player_id": pid, "position": pos, "recent_team": "AAA", "season": 2026,
                         "week": w, "season_type": "REG", "targets": tg, "receptions": tg * 0.65,
                         "receiving_yards": ry, "carries": 15 if pos == "RB" else 0,
                         "rushing_yards": 65 if pos == "RB" else 0, "attempts": 0,
                         "completions": 0, "passing_yards": 0})
        rows.append({"player_id": "QB1", "position": "QB", "recent_team": "AAA", "season": 2026,
                     "week": w, "season_type": "REG", "targets": 0, "receptions": 0,
                     "receiving_yards": 0, "carries": 3, "rushing_yards": 12, "attempts": 34,
                     "completions": 22, "passing_yards": 245})
    return pd.DataFrame(rows)


def test_opportunity_profiles_and_projection():
    w = om.prepare_weekly([_weekly()])
    prof = om.player_profiles(w, 2026)
    team = om.team_volume_ewm(w)["AAA"]
    assert prof["WR1"]["tgt_sh_e"] == pytest.approx(9 / 16)
    hi = om.project(prof["WR1"], "receiving_yards", team, imp_pts=28, spread_team=-7)
    lo = om.project(prof["WR1"], "receiving_yards", team, imp_pts=17, spread_team=10)
    assert hi["median"] > lo["median"]  # trailing, high-total script → more targets
    assert om.project(prof["RB1"], "passing_yards", team, imp_pts=22, spread_team=0) is None
    assert om.project(prof["QB1"], "passing_yards", team, imp_pts=22, spread_team=0)["median"] > 150


# ---- data source + gating fixes --------------------------------------------------


def test_legacy_weekly_mapping_and_seasonal_shares():
    raw = pd.DataFrame({
        "player_id": ["A", "A", "B"], "player_name": ["a", "a", "b"], "position": ["WR"] * 3,
        "season": [2026] * 3, "week": [1, 2, 1], "season_type": ["REG"] * 3,
        "team": ["AAA"] * 3, "opponent_team": ["BBB"] * 3,
        "passing_interceptions": [0, 0, 0], "sacks_suffered": [0, 0, 0], "sack_yards_lost": [0, 0, 0],
        "targets": [10, 6, 10], "receptions": [7, 4, 5], "receiving_yards": [100, 50, 60],
        "attempts": [0, 0, 0], "wopr": [0.9, 0.5, 0.4],
    })
    wk = nflverse_stats.to_legacy_weekly(raw)
    assert {"recent_team", "interceptions", "sacks", "sack_yards"} <= set(wk.columns)
    szn = nflverse_stats.seasonal_from_weekly(raw).set_index("player_id")
    assert szn.loc["A", "games"] == 2 and szn.loc["A", "targets"] == 16
    assert "wopr" in szn.columns and "wopr_x" not in szn.columns


def test_season_gate_uses_published_data_not_calendar(monkeypatch):
    from app.services import player_predictions_service as pps

    async def frame(season):
        return pd.DataFrame({"week": [1, 2, 3]}) if season == 2026 else None

    monkeypatch.setattr(pps, "_player_weekly_frame", frame)
    monkeypatch.setattr(pps, "current_or_upcoming_season", lambda: 2026)
    assert asyncio.run(pps._season_has_weekly(2026)) is True
    assert asyncio.run(pps._season_has_weekly(2027)) is False
    assert asyncio.run(pps._defense_season(2026)) == 2026


def test_market_edge_reports_realized_share():
    from app.services import market_service as ms

    pred = ps.predict_game(1500, 1500, v5=_v5_payload(margin=6.0, total=44.0))
    ms.apply_market_blend(pred, {"consensus_home_prob": 0.55, "spread_home": -2.0,
                                 "total": 44.0, "effective_sources": 3})
    assert pred["edge"]["spread"] == pytest.approx(-4.0)
    assert pred["edge"]["spread_expected_pts"] == pytest.approx(-0.4)
    assert pred["edge"]["total_expected_pts"] == pytest.approx(0.0)


def test_project_stat_for_game_opportunity_path_ships_median_and_quantiles():
    from app.services import player_predictions_service as pps
    from app.services import player_projection_engine as engine

    w = om.prepare_weekly([_weekly()])
    opp = {"profile": om.player_profiles(w, 2026)["WR1"],
           "team_ewm": om.team_volume_ewm(w)["AAA"], "role_mult": 1.0}
    env = {"exp_pts_for": 24.0, "exp_pts_against": 21.0}
    post = engine.StatPosterior(mean=80.0, game_sd=30.0, talent_sd=5.0, prior_n=8, obs_n=6)
    out = pps._project_stat_for_game(post, "receiving_yards", env, 1.0, None, 1.0, opp=opp)
    assert out["basis"] == om.MODEL_VERSION
    assert out["predicted"] == pytest.approx(out["median"], abs=0.5)
    assert out["median"] < out["mean"]
    assert {"low", "high", "sd", "interval_80", "env_multiplier"} <= set(out)
    p_med = engine.projection_over_prob(out, out["median"])
    assert p_med == pytest.approx(0.5, abs=0.02)
    # TDs are not in the opportunity model → posterior path.
    td = pps._project_stat_for_game(
        engine.StatPosterior(mean=0.5, game_sd=0.6, talent_sd=0.1, prior_n=12, obs_n=6),
        "receiving_tds", env, 1.0, None, 1.0, opp=opp)
    assert "quantiles" not in td
