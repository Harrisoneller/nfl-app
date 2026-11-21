"""Synthetic checks for the player allocation. No season is scored."""

from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pandas as pd

from research.nfl_drive_sim.market import strip_market_columns
from research.nfl_drive_sim.player_layer import (
    allocate_team,
    largest_remainder,
    rows_before_kickoff,
)

ROOT = Path(__file__).resolve().parents[2] / "research" / "nfl_drive_sim"


def test_largest_remainder_hits_the_total_and_does_not_invent_shares():
    weights = np.array([0.2, 0.3, 0.5])
    total = np.array([10, 1, 0])
    out = largest_remainder(weights, total)
    assert np.array_equal(out.sum(axis=1), total)
    empty = largest_remainder(np.zeros(3), np.array([4, 0]))
    assert np.array_equal(empty[0], np.zeros(3, dtype=int))
    assert np.array_equal(empty.sum(axis=1), np.array([0, 0]))


def test_player_totals_reconcile_to_the_team_draw():
    roster = pd.DataFrame([
        {"player_id": "q", "position": "QB", "att_sh": 1.0, "car_sh": 0.0, "tgt_sh": 0.0,
         "comp": 0.65, "cr": 0.0, "ypa": 7.0, "ypc": 0.0, "ypt": 0.0, "ptd": 0.04, "rutd": 0.0, "retd": 0.0},
        {"player_id": "r", "position": "RB", "att_sh": 0.0, "car_sh": 0.7, "tgt_sh": 0.15,
         "comp": 0.0, "cr": 0.75, "ypa": 0.0, "ypc": 4.2, "ypt": 6.0, "ptd": 0.0, "rutd": 0.03, "retd": 0.02},
        {"player_id": "b", "position": "RB", "att_sh": 0.0, "car_sh": 0.3, "tgt_sh": 0.05,
         "comp": 0.0, "cr": 0.70, "ypa": 0.0, "ypc": 4.0, "ypt": 5.0, "ptd": 0.0, "rutd": 0.02, "retd": 0.01},
        {"player_id": "w", "position": "WR", "att_sh": 0.0, "car_sh": 0.4, "tgt_sh": 0.50,
         "comp": 0.0, "cr": 0.62, "ypa": 0.0, "ypc": 8.0, "ypt": 8.5, "ptd": 0.0, "rutd": 0.1, "retd": 0.04},
        {"player_id": "t", "position": "TE", "att_sh": 0.0, "car_sh": 0.0, "tgt_sh": 0.30,
         "comp": 0.0, "cr": 0.68, "ypa": 0.0, "ypc": 0.0, "ypt": 7.5, "ptd": 0.0, "rutd": 0.0, "retd": 0.03},
    ])
    plays = np.array([55, 62, 48, 0, 71])
    packed = allocate_team(plays, roster, {"pass_rate": 0.61, "sack_rate": 0.07})
    team = packed["_team"]
    ids = [pid for pid in packed if pid != "_team"]
    for stat in (
        "attempts", "completions", "passing_yards", "passing_tds",
        "carries", "rushing_yards", "rushing_tds",
        "targets", "receptions", "receiving_yards", "receiving_tds",
    ):
        total = sum(packed[pid][stat] for pid in ids)
        assert np.allclose(total, team[stat]), stat
    assert np.array_equal(team["attempts"], team["attempts_from_plays"])
    assert np.array_equal(team["carries"], team["carries_from_plays"])
    assert np.array_equal(team["targets"], team["targets_from_plays"])
    assert np.array_equal(team["attempts"] + team["carries"], plays)
    # Carries stay on running backs. A wideout carry share cannot take them.
    assert np.array_equal(packed["w"]["carries"], np.zeros(len(plays), dtype=int))
    assert np.all(packed["q"]["completions"] <= packed["q"]["attempts"])
    assert np.all(packed["w"]["receptions"] <= packed["w"]["targets"])


def test_cutoff_excludes_an_equal_kickoff():
    kick = pd.Timestamp("2018-09-09 17:00", tz="UTC")
    frame = pd.DataFrame({
        "kickoff_et": [kick - pd.Timedelta(hours=1), kick, kick + pd.Timedelta(hours=1)],
        "player_id": ["earlier", "equal", "later"],
    })
    kept = rows_before_kickoff(frame, kick)
    assert list(kept["player_id"]) == ["earlier"]


def test_market_columns_are_stripped_and_the_layer_does_not_name_them():
    frame = pd.DataFrame({"player_id": ["a"], "targets": [3], "spread_line": [-3.0], "moneyline": [-110]})
    cleaned, removed = strip_market_columns(frame)
    assert removed == ["spread_line", "moneyline"]
    assert "spread_line" not in cleaned.columns
    text = (ROOT / "player_layer.py").read_text()
    tree = ast.parse(text)
    for name in ("app.services", "team_ratings_v5", "build_week_model", "market_ratings", "backtest_v5"):
        assert name not in text
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "app" not in node.module.split(".")
