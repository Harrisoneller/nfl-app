"""Synthetic checks for the no-market v5 replay. No real season is scored."""

from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from research.nfl_drive_sim import v5_replay
from research.nfl_drive_sim.v5_replay import (
    PARAMS_SHA256,
    bootstrap_rng,
    discrete_crps,
    drop_rate_voids,
    empirical_pmf,
    game_dropped,
    normal_integer_pmf,
    ratings_as_of,
    replay_week,
    resolve_qb,
    sample_sd,
    sim_seed,
    webb_mean_interval,
)

EASTERN = ZoneInfo("America/New_York")
REPLAY = Path(v5_replay.__file__)
BANNED = {
    "build_week_model",
    "market_ratings",
    "backtest_v5",
    "MARGIN_COEFS",
    "MARGIN_COEFS_NO_MKT",
    "TOTAL_COEFS",
}
PINNED_PARAMS_SHA = "f2e0072102c421144df23dceec743c7b6eab51d17858be4279709ad5238eb22a"


def _calls(tree: ast.AST) -> set[str]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                found.add(func.id)
            elif isinstance(func, ast.Attribute):
                found.add(func.attr)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found.add(node.module)
            found.update(alias.name for alias in node.names)
    return found


def test_banned_functions_and_market_columns_are_not_reachable():
    source = REPLAY.read_text()
    tree = ast.parse(source)
    assert _calls(tree).isdisjoint(BANNED)
    assert "spread_line" not in source
    assert "mkt_d" not in source
    assert PARAMS_SHA256 == PINNED_PARAMS_SHA


def test_replay_week_rejects_a_short_window_and_strips_a_market_column():
    rows = []
    for index in range(32):
        epa = float(index - 16)
        rows.append({
            "epa_d": epa, "pts_d": 0.0, "st_d": 0.0, "qb_d": 0.0, "hfa": 1.0,
            "pts_s": 1.0, "epa_s": 0.0, "env2": 0.0, "plays_s": 0.0, "qb_s": 0.0,
            "margin": 2.0 * epa + 1.0,
            "total": 7.0,
            "spread_line": 99.0,
        })
    train = pd.DataFrame(rows)
    test = train.iloc[:1].drop(columns=["margin", "total"])
    short = replay_week(train.iloc[:31].drop(columns=["spread_line"]), test.drop(columns=["spread_line"]))
    assert short["ok"] is False
    assert short["reason"] == "underidentified"
    stripped = replay_week(train.assign(moneyline=1.0), test.assign(moneyline=1.0))
    assert stripped["ok"] is True


def test_predictions_match_the_synthetic_line():
    rows = []
    for index in range(32):
        epa = float(index - 16)
        rows.append({
            "epa_d": epa, "pts_d": 0.0, "st_d": 0.0, "qb_d": 0.0, "hfa": 1.0,
            "pts_s": 2.0, "epa_s": 0.0, "env2": 0.0, "plays_s": 0.0, "qb_s": 0.0,
            "margin": 2.0 * epa + 1.0,
            "total": 3.0,
        })
    train = pd.DataFrame(rows)
    test = pd.DataFrame([{
        "epa_d": 3.0, "pts_d": 0.0, "st_d": 0.0, "qb_d": 0.0, "hfa": 1.0,
        "pts_s": 2.0, "epa_s": 0.0, "env2": 0.0, "plays_s": 0.0, "qb_s": 0.0,
    }])
    fitted = replay_week(train, test)
    assert abs(fitted["margin"][0] - 7.0) < 1e-8
    assert abs(fitted["total"][0] - 3.0) < 1e-8


def test_integer_crps_and_sample_sd():
    point = np.zeros(161)
    point[80] = 1.0
    assert discrete_crps(point, 0.0) == 0.0
    mass = normal_integer_pmf(0.0, 13.5)
    assert mass is not None
    assert abs(mass.sum() - 1.0) < 1e-12
    assert abs(mass[80] - mass[80]) < 1e-12
    assert abs(mass[70] - mass[90]) < 1e-12
    draws = empirical_pmf(np.array([0.0, 0.0, 3.0, -3.0]))
    assert draws is not None
    assert abs(draws.sum() - 1.0) < 1e-12
    assert sample_sd(np.array([1.0, 2.0, 3.0])) == 1.0
    assert sample_sd(np.array([1.0])) is None


def test_missing_quarterback_drops_both_sides_and_the_rate_voids():
    kick = datetime(2016, 9, 11, 13, 0, tzinfo=EASTERN)
    older = datetime(2015, 12, 20, 13, 0, tzinfo=EASTERN)
    qb, source = resolve_qb(None, [(older, "QB-PRIOR")])
    assert (qb, source) == ("QB-PRIOR", "prior_start")
    chart, chart_source = resolve_qb("QB-CHART", [(kick, "QB-LATER")])
    assert (chart, chart_source) == ("QB-CHART", "depth_chart")
    missing, reason = resolve_qb(None, [])
    assert missing is None and reason == "missing"
    assert game_dropped(None, "QB-B", True, True) == "missing_qb"
    assert game_dropped("QB-A", "QB-B", False, True) == "model_failure"
    assert game_dropped("QB-A", "QB-B", True, True) is None
    assert drop_rate_voids(3, 100) is True
    assert drop_rate_voids(2, 100) is False


def test_seeds_are_deterministic_and_the_bootstrap_stream_is_separate():
    assert sim_seed("2018_01_ATL_PHI") == sim_seed("2018_01_ATL_PHI")
    assert sim_seed("2018_01_ATL_PHI") != sim_seed("2018_01_BUF_BAL")
    values = np.arange(6, dtype=float)
    clusters = np.array([1, 1, 2, 2, 3, 3])
    first = webb_mean_interval(values, clusters, bootstrap_rng(), reps=30)
    second = webb_mean_interval(values, clusters, bootstrap_rng(), reps=30)
    assert first == second
    assert first[1] <= first[0] <= first[2]


def test_ratings_use_the_frozen_helpers_on_synthetic_rows():
    kick = pd.Timestamp(datetime(2015, 9, 13, 13, 0, tzinfo=EASTERN))
    later = pd.Timestamp(datetime(2015, 9, 20, 13, 0, tzinfo=EASTERN))
    rows = []
    for week, stamp, home in ((1, kick, True), (2, later, False)):
        rows.append({
            "season": 2015, "week": week, "kickoff_et": stamp,
            "posteam": "H" if home else "A", "defteam": "A" if home else "H",
            "is_home": 1.0 if home else 0.0, "pts": 21.0, "n": 60.0,
            "gw": 50.0, "w_epa": 5.0, "p_w": 30.0, "p_epa": 3.0, "st_epa": 0.1,
            "qb": "QB1", "spread_line": -3.0,
        })
    ratings, book = ratings_as_of(pd.DataFrame(rows), 2015, 3, ["H", "A"], later)
    assert "epa" in ratings
    assert book.replacement == -0.10
