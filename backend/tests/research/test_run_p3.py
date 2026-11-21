"""The P3 runner on a synthetic slate. The frozen game set is counted, not scored."""

from __future__ import annotations

import ast
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from research.nfl_drive_sim import sim_config as cfg
from research.nfl_drive_sim.run_p3 import (
    PRIMARY_N,
    GameSetError,
    RealRunLocked,
    assign_quarterbacks,
    load_game_set,
    rows_before_cutoff,
    run_real,
    score_slate,
    write_results,
)
from research.nfl_drive_sim.simulate import simulate_game
from research.nfl_drive_sim.v5_replay import MARGIN_COLUMNS, TOTAL_COLUMNS

EASTERN = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[2] / "research" / "nfl_drive_sim"
GAME_SET = ROOT / "outputs" / "p3" / "game_set.csv"
RUNNER = Path(__file__).resolve().parents[2] / "research" / "nfl_drive_sim" / "run_p3.py"


def test_frozen_primary_set_is_2534_and_a_short_file_aborts(tmp_path):
    frame = load_game_set(GAME_SET)
    primary = frame[frame["role"].eq("primary")]
    assert len(primary) == PRIMARY_N == 2534
    assert primary["season"].isin([2016, 2017, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026]).all()
    assert 2018 not in set(primary["season"])
    assert primary.iloc[-1]["game_id"] == "2026_11_IND_HOU"
    assert set(frame.loc[frame["role"].eq("playoff"), "game_type"]) <= {"WC", "DIV", "CON", "SB"}
    path = tmp_path / "short.csv"
    primary.iloc[:-1].to_csv(path, index=False)
    with pytest.raises(GameSetError):
        load_game_set(path)


def test_same_size_swap_including_a_2018_game_aborts(tmp_path):
    frame = pd.read_csv(GAME_SET)
    primary = frame.index[frame["role"].eq("primary")]
    frame.loc[primary[0], "game_id"] = "2018_01_ATL_PHI"
    assert int(frame["role"].eq("primary").sum()) == PRIMARY_N
    path = tmp_path / "swapped.csv"
    frame.to_csv(path, index=False)
    with pytest.raises(GameSetError):
        load_game_set(path)


def test_preweek_cutoff_excludes_an_equal_kickoff():
    kick = pd.Timestamp("2016-09-11 17:00", tz="UTC")
    frame = pd.DataFrame({
        "kickoff_et": [kick - pd.Timedelta(hours=1), kick, kick + pd.Timedelta(hours=1)],
        "game_id": ["earlier", "equal", "later"],
    })
    kept = rows_before_cutoff(frame, kick)
    assert list(kept["game_id"]) == ["earlier"]


def test_prior_start_is_used_when_the_chart_is_missing():
    charts = pd.DataFrame([
        {"season": 2015, "week": 1, "team": "H", "game_type": "REG", "position": "QB", "depth_team": "1", "gsis_id": "QB-OLD"},
        {"season": 2015, "week": 1, "team": "A", "game_type": "REG", "position": "QB", "depth_team": "1", "gsis_id": "QB-A"},
    ])
    games = pd.DataFrame([
        {"game_id": "g1", "season": 2015, "week": 1, "game_type": "REG", "kickoff_et": "2015-09-13T17:00:00Z", "home_team": "H", "away_team": "A"},
        {"game_id": "g2", "season": 2015, "week": 2, "game_type": "REG", "kickoff_et": "2015-09-20T17:00:00Z", "home_team": "H", "away_team": "A"},
    ])
    out = assign_quarterbacks(games, charts)
    second = out.loc[out["game_id"].eq("g2")].iloc[0]
    assert second["home_qb"] == "QB-OLD"
    assert second["home_qb_source"] == "prior_start"


def test_real_run_is_locked_and_the_runner_does_not_reach_market_code():
    with pytest.raises(RealRunLocked):
        run_real()
    tree = ast.parse(RUNNER.read_text())
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            called.add(func.id if isinstance(func, ast.Name) else getattr(func, "attr", ""))
        elif isinstance(node, ast.ImportFrom) and node.module:
            called.add(node.module)
    banned = {"build_week_model", "market_ratings", "backtest_v5", "urlopen", "spread_line"}
    assert called.isdisjoint(banned)


def _feature(game_id, season, week, kick, epa, pts):
    row = {
        "game_id": game_id,
        "season": season,
        "week": week,
        "kickoff_et": kick,
        "game_type": "REG",
        "margin": 2.0 * epa + 1.0,
        "total": 3.0 * pts,
        "epa_d": epa,
        "pts_d": 0.0,
        "st_d": 0.0,
        "qb_d": 0.0,
        "hfa": 1.0,
        "pts_s": pts,
        "epa_s": 0.0,
        "env2": 0.0,
        "plays_s": 0.0,
        "qb_s": 0.0,
    }
    assert set(MARGIN_COLUMNS).issubset(row)
    assert set(TOTAL_COLUMNS).issubset(row)
    return row


def _history():
    rows = []
    start = datetime(2015, 9, 13, 13, 0, tzinfo=EASTERN)
    for index in range(32):
        row = _feature(
            f"h1-{index}", 2015, 1, start + timedelta(minutes=index),
            float(index - 16), 1.0 + index / 32,
        )
        row["total"] = row["total"] + (index % 5) * 0.7
        rows.append(row)
    later = datetime(2015, 9, 20, 13, 0, tzinfo=EASTERN)
    for index in range(32):
        row = _feature(
            f"h2-{index}", 2015, 2, later + timedelta(minutes=index),
            float(index - 16), 2.0,
        )
        row["margin"] = row["margin"] + (index % 4) * 0.5
        row["total"] = row["total"] + (index % 3) * 1.1
        rows.append(row)
    return rows


def test_score_slate_on_a_synthetic_week_writes_d_without_the_cache(tmp_path):
    kick = datetime(2016, 9, 11, 13, 0, tzinfo=EASTERN)
    features = _history()
    games = []
    for index, epa in enumerate((1.0, -2.0)):
        feat = _feature(f"s-{index}", 2016, 1, kick + timedelta(hours=index), epa, 4.0)
        margin = feat["margin"]
        total = feat["total"]
        feat.update({
            "role": "primary",
            "home_team": "H",
            "away_team": "A",
            "home_score": (total + margin) / 2,
            "away_score": (total - margin) / 2,
            "home_qb": "QB-H",
            "away_qb": "QB-A",
        })
        features.append(feat)
        games.append(feat)

    def card_for(_game, ypp_on):
        return {
            "fixed_plays": 1,
            "fixed_sec": 400.0,
            "forced_outcome": cfg.DRIVE_OUTCOMES.index("PUNT"),
            "receive_home": True,
            "banks": {"KICKOFF": np.array([75.0]), "PUNT": np.array([80.0])},
            "pat_rate": 0.0,
            "p_punt_return_td": 0.0,
            "p_to_return_td": 0.0,
            "ypp_layer": ypp_on,
            "max_drives": 12,
        }
    result = score_slate(
        pd.DataFrame(games),
        pd.DataFrame(features),
        simulate_game,
        n_sims=2,
        card_for=card_for,
        reps=30,
    )
    assert result["n_primary"] == 2
    assert result["n_dropped"] == 0
    assert result["decision"] in {"SUCCESS", "FAILURE", "INCONCLUSIVE"}
    scored = result["games"]
    assert len(scored) == 4
    assert scored["d_margin_crps"].notna().all()
    assert scored["d_total_crps"].notna().all()
    assert scored["d_margin_mae"].notna().all()
    assert scored["d_total_mae"].notna().all()
    assert scored["d_brier"].notna().all()
    assert set(scored["flag_margin"]).issubset({0.0, 1.0})
    assert scored["margin_at_5min"].notna().all()
    (tmp_path / "manifest.sha256").write_text("abc  game_set.csv\n")
    write_results(tmp_path, result)
    manifest = (tmp_path / "manifest.sha256").read_text()
    assert "abc  game_set.csv" in manifest
    assert "per_game_d.csv" in manifest
    assert (tmp_path / "p3_report.md").read_text().startswith("# P3 report\n\nP3 is not a fair ATS")
    assert (tmp_path / "per_game_d.csv").exists()
    assert (tmp_path / "manifest.sha256").exists()
    games[0]["home_qb"] = None
    voided = score_slate(
        pd.DataFrame(games),
        pd.DataFrame(features),
        simulate_game,
        n_sims=1,
        card_for=card_for,
        reps=10,
    )
    assert voided["void"] is True
    assert voided["decision"] == "VOID"
