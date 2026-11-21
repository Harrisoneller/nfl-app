"""Synthetic checks for the 2018 drive sim. No season other than the fixture is scored."""

from __future__ import annotations

import ast
import itertools
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from research.nfl_drive_sim import sim_config as cfg
from research.nfl_drive_sim.calendar import prepare_schedule
from research.nfl_drive_sim.market import assert_no_market_columns
from research.nfl_drive_sim.priors import prior_slice, with_priors
from research.nfl_drive_sim.run_p2 import assert_dev_season
from research.nfl_drive_sim.simulate import simulate_game

EASTERN = ZoneInfo("America/New_York")


def _kick(hour: int) -> pd.Timestamp:
    return pd.Timestamp(datetime(2018, 9, hour, 13, 0, tzinfo=EASTERN))


def _history() -> pd.DataFrame:
    rows = []
    for index, plays in enumerate((50, 60, 70), start=1):
        rows.append({
            "game_id": f"g{index}",
            "kickoff_et": _kick(index),
            "team": "A",
            "opponent": "B",
            "plays": plays,
            "drives": 10,
            "sec_per_play": 20 + index,
            "plays_per_drive": 6,
            "no_huddle": 0.1,
            "incomplete": 0.3,
            "pass_rate": 0.5,
            "epa": 0.1 * index,
            "success": 0.4,
            "expl_pass": 0.1,
            "expl_run": 0.1,
            "early_pass": 0.5,
            "sack": 0.05,
            "pressure": 0.2,
            "ypp_pass": 6 + index,
            "ypp_run": 4,
            "td_rate": 0.2,
            "fg_rate": 0.1,
            "punt_rate": 0.4,
            "to_rate": 0.1,
            "sec_neutral": 24,
            "sec_leading": 28,
            "sec_trailing": 22,
            "sec_two_minute": 18,
            "ppd_neutral": 6,
            "ppd_leading": 5,
            "ppd_trailing": 7,
            "ppd_two_minute": 4,
            "qb_id": "QB_A",
        })
    return pd.DataFrame(rows)


def _card(ypp_pass: float = 0.0, ypp_on: bool = False) -> dict:
    return {
        "fixed_plays": 4,
        "fixed_sec": 30.0,
        "forced_outcome": cfg.DRIVE_OUTCOMES.index("PUNT"),
        "receive_home": True,
        "banks": {"KICKOFF": np.array([75.0]), "PUNT": np.array([80.0])},
        "pat_rate": 0.0,
        "p_punt_return_td": 0.0,
        "p_to_return_td": 0.0,
        "ypp_layer": ypp_on,
        "dome": 0.0,
        "home_context": {"ypp_pass": ypp_pass, "ypp_run": 0.0, "off_td": 0.2, "qb_epa": 0.1},
        "away_context": {"ypp_pass": ypp_pass, "ypp_run": 0.0, "off_td": 0.2, "qb_epa": 0.0},
        "max_drives": 40,
    }


def test_schedule_keeps_roof_wind_and_overtime():
    frame = pd.DataFrame([{
        "game_id": "2018_01_A_B",
        "season": 2018,
        "week": 1,
        "game_type": "REG",
        "gameday": "2018-09-09",
        "gametime": "13:00",
        "home_team": "B",
        "away_team": "A",
        "home_score": 20,
        "away_score": 17,
        "roof": "dome",
        "temp": 72,
        "wind": 0,
        "overtime": 1,
        "spread_line": -3,
    }])
    kept = prepare_schedule(frame).games
    assert kept.iloc[0]["roof"] == "dome"
    assert float(kept.iloc[0]["wind"]) == 0
    assert float(kept.iloc[0]["overtime"]) == 1
    assert "spread_line" not in kept.columns


def test_dev_season_is_2018_only():
    assert_dev_season(2018)
    with pytest.raises(ValueError):
        assert_dev_season(2019)
    with pytest.raises(ValueError):
        assert_dev_season(2017)


def test_prior_rows_exclude_the_target_game_and_later_games():
    history = _history()
    target = history.iloc[1]
    kept = prior_slice(history, target.kickoff_et, game_id=target.game_id)
    assert list(kept["game_id"]) == ["g1"]
    changed = history.copy()
    changed.loc[changed["game_id"].eq("g3"), "plays"] = 999
    again = prior_slice(changed, target.kickoff_et, game_id=target.game_id)
    assert list(again["plays"]) == [50]
    empty_qb = pd.DataFrame(columns=["game_id", "kickoff_et", "qb_id", "epa", "cpoe", "dropbacks"])
    featured = with_priors(history, empty_qb)
    future = history.copy()
    future.loc[future["game_id"].eq("g3"), "plays"] = 999
    future.loc[future["game_id"].eq("g3"), "epa"] = 9
    again_featured = with_priors(future, empty_qb)
    target_row = featured[featured["game_id"].eq("g2")].iloc[0]
    again_row = again_featured[again_featured["game_id"].eq("g2")].iloc[0]
    assert target_row["off_plays"] == again_row["off_plays"]
    assert target_row["off_epa"] == again_row["off_epa"]
    earlier = history.copy()
    earlier.loc[earlier["game_id"].eq("g1"), "plays"] = 10
    changed = with_priors(earlier, empty_qb)
    assert changed[changed["game_id"].eq("g2")].iloc[0]["off_plays"] != target_row["off_plays"]
    assert "drive_first_passer_id" not in featured.columns
    assert_no_market_columns(featured, "priors")


def test_sims_are_deterministic_for_a_seed():
    card = _card()
    first = simulate_game(card, 30, np.random.default_rng(7))
    second = simulate_game(card, 30, np.random.default_rng(7))
    assert np.array_equal(first["margin"], second["margin"])
    assert np.array_equal(first["home_plays"], second["home_plays"])


def test_possession_accounting_and_clock():
    result = simulate_game(_card(), 4, np.random.default_rng(1))
    assert np.all(np.abs(result["home_drives"] - result["away_drives"]) <= 1)
    assert np.all(result["home_plays"] == result["home_drives"] * 4)
    assert np.all(result["away_plays"] == result["away_drives"] * 4)
    trace = result["half_trace"]
    for earlier, later in itertools.pairwise(trace):
        reset = np.isin(later, [cfg.REGULATION_HALF_SECONDS, cfg.OT_SECONDS])
        assert np.all((later <= earlier + 1e-6) | reset)


def test_every_drive_outcome_is_reachable():
    weights = np.zeros((2, len(cfg.DRIVE_OUTCOMES)))
    model = {"weights": weights, "mu": np.zeros(2), "sd": np.ones(2), "names": ["off_td"]}
    card = _card()
    card.pop("forced_outcome")
    card["drive_model"] = model
    card["max_drives"] = 1
    card["fixed_plays"] = 1
    card["fixed_sec"] = 1.0
    result_outcomes = []
    rng = np.random.default_rng(11)
    for _ in range(400):
        one = simulate_game(card, 1, rng)
        # The single drive's points identify the outcome class used.
        result_outcomes.append((float(one["home_points"][0]), float(one["away_points"][0])))
    # Uniform logits reach a scoring and a non-scoring drive across draws.
    assert len(set(result_outcomes)) >= 3


def test_ypp_flag_off_ignores_the_yards_input():
    weights = np.zeros((2, len(cfg.DRIVE_OUTCOMES)))
    weights[1, cfg.DRIVE_OUTCOMES.index("TD")] = 5.0
    model = {"weights": weights, "mu": np.zeros(2), "sd": np.ones(2), "names": ["ypp_pass"]}
    left = _card(ypp_pass=0.0, ypp_on=False)
    right = _card(ypp_pass=9.0, ypp_on=False)
    for card in (left, right):
        card.pop("forced_outcome")
        card["drive_model"] = model
        card["max_drives"] = 2
    a = simulate_game(left, 20, np.random.default_rng(3))
    b = simulate_game(right, 20, np.random.default_rng(3))
    assert np.array_equal(a["total"], b["total"])


def _clock_card() -> dict:
    from research.nfl_drive_sim.clock import _empty_clock, fit_scrimmage_lengths
    card = _card()
    card.pop("forced_outcome")
    card["clock"] = _empty_clock()
    card["clock"]["means"] = {
        (play_class, state): {
            "pass_incomplete": 8.0,
            "pass_complete": 30.0,
            "kickoff": 3.0,
            "punt": 10.0,
            "field_goal": 4.0,
            "extra_point": 0.0,
        }.get(play_class, 20.0)
        for play_class in (
            "pass_complete", "pass_incomplete", "pass_oob", "run", "run_oob", "sack",
            "spike", "kneel", "punt", "kickoff", "field_goal", "extra_point", "no_play",
        )
        for state in cfg.SITUATIONS
    }
    card["lengths"] = fit_scrimmage_lengths(pd.DataFrame(), pd.DataFrame())
    card["home_pace"] = {state: {"plays": 4.0, "sec": 8.0} for state in cfg.SITUATIONS}
    card["away_pace"] = {state: {"plays": 4.0, "sec": 8.0} for state in cfg.SITUATIONS}
    card["home_pass"] = 1.0
    card["away_pass"] = 1.0
    card["home_inc"] = 1.0
    card["away_inc"] = 1.0
    card["home_sack"] = 0.0
    card["away_sack"] = 0.0
    card["forced_outcome"] = cfg.DRIVE_OUTCOMES.index("PUNT")
    card.pop("fixed_plays")
    card.pop("fixed_sec")
    return card


def test_incomplete_passes_stop_the_clock_and_special_teams_are_separate():
    """Incompletions use the stopped-clock mean. The kick and the punt are extra."""
    card = _clock_card()
    card["home_pace"] = {state: {"plays": 3.0, "sec": 8.0} for state in cfg.SITUATIONS}
    card["away_pace"] = card["home_pace"]
    card["lengths"]["length"] = {
        (outcome, state): 3.0 for outcome in cfg.DRIVE_OUTCOMES for state in cfg.SITUATIONS
    }
    card["lengths"]["league_ppd"] = {state: 3.0 for state in cfg.SITUATIONS}
    card["max_drives"] = 2
    result = simulate_game(card, 300, np.random.default_rng(1))
    used = result["half_trace"][0] - result["half_trace"][1]
    # Kick 3 + about three incompletes at 8 + punt 10. A running-clock pass is 30.
    assert used.mean() < 50
    assert used.mean() > 25
    assert result["home_plays"].mean() == pytest.approx(3.0, abs=0.4)


def test_points_cover_scores_returns_and_safeties():
    def one(outcome: str, **rates) -> tuple[float, float]:
        card = _card()
        card["forced_outcome"] = cfg.DRIVE_OUTCOMES.index(outcome)
        card["max_drives"] = 1
        card["fixed_plays"] = 1
        card["fixed_sec"] = 1.0
        card.update(rates)
        result = simulate_game(card, 1, np.random.default_rng(1))
        return float(result["home_points"][0]), float(result["away_points"][0])

    assert one("TD", pat_rate=1.0) == (7.0, 0.0)
    assert one("TD", pat_rate=0.0) == (6.0, 0.0)
    assert one("FG_MADE") == (3.0, 0.0)
    assert one("FG_MISSED") == (0.0, 0.0)
    assert one("SAFETY") == (0.0, 2.0)
    assert one("PUNT", p_punt_return_td=1.0) == (0.0, 7.0)
    assert one("TURNOVER", p_to_return_td=1.0) == (0.0, 7.0)


def test_second_half_starts_over_and_the_other_team_receives():
    card = _card()
    card["fixed_plays"] = 1
    card["fixed_sec"] = cfg.REGULATION_HALF_SECONDS
    card["max_drives"] = 2
    result = simulate_game(card, 1, np.random.default_rng(1))
    assert result["home_drives"][0] == 1
    assert result["away_drives"][0] == 1
    assert result["half_trace"][0][0] == cfg.REGULATION_HALF_SECONDS
    assert result["half_trace"][1][0] == cfg.REGULATION_HALF_SECONDS


def test_softmax_recovers_a_state_that_ends_the_half():
    from research.nfl_drive_sim.models import fit_softmax, softmax
    rng = np.random.default_rng(0)
    two = rng.random(3000) < 0.15
    outcome = np.where(two, cfg.DRIVE_OUTCOMES.index("END_OF_HALF"), cfg.DRIVE_OUTCOMES.index("PUNT"))
    x = np.column_stack([np.ones(len(two)), two.astype(float)])
    sd = x.std(axis=0)
    sd[sd < 1e-6] = 1.0
    sd[0] = 1.0
    xs = x / sd
    weights = fit_softmax(xs, outcome, len(cfg.DRIVE_OUTCOMES), cfg.DRIVE_LOGIT_L2, cfg.DRIVE_LOGIT_ITERS)
    probs = softmax(xs @ weights)
    end = cfg.DRIVE_OUTCOMES.index("END_OF_HALF")
    assert probs[two, end].mean() > 0.7
    assert probs[~two, end].mean() < 0.15


def test_package_source_has_no_market_or_served_model_import():
    banned = ("team_ratings_v5", "app.services")
    root = Path(__file__).resolve().parents[2] / "research" / "nfl_drive_sim"
    offenders = []
    for path in sorted(root.rglob("*.py")):
        text = path.read_text()
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if called not in {"import_module", "__import__"}:
                continue
            rendered = ast.unparse(node)
            if any(name in rendered for name in ("team_ratings_v5", "app.services")):
                offenders.append(f"{path.name}:{node.lineno}")
        for name in banned:
            if name in text:
                offenders.append(f"{path.name} mentions {name}")
    assert offenders == []
