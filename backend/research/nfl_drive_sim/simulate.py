"""Vectorized game sim.

2018 overtime, simplified. A new coin flip gives one team the ball for a
10-minute period. A touchdown on that first possession wins immediately.
Anything else gives the opponent a possession. After both teams have
possessed, the next score wins. If the period expires with the score tied,
the game is a tie. A playoff game that is still tied gets one more
10-minute sudden-death period and then stops, so a sim cannot run forever.
The 2018 regular season did allow ties. Possessions are an output of the
clock, not an input, unless an oracle pace is supplied.
"""

from __future__ import annotations

import numpy as np

from . import sim_config as cfg
from .p2_fit import outcome_probabilities

TD = cfg.DRIVE_OUTCOMES.index("TD")
FG = cfg.DRIVE_OUTCOMES.index("FG_MADE")
PUNT = cfg.DRIVE_OUTCOMES.index("PUNT")
TURNOVER = cfg.DRIVE_OUTCOMES.index("TURNOVER")
SAFETY = cfg.DRIVE_OUTCOMES.index("SAFETY")


def situation_array(score_diff: np.ndarray, half_seconds: np.ndarray) -> np.ndarray:
    labels = np.full(score_diff.shape, "neutral", dtype=object)
    two = half_seconds <= cfg.TWO_MINUTE_SECONDS
    labels = np.where((score_diff > cfg.NEUTRAL_SCORE) & ~two, "leading", labels)
    labels = np.where((score_diff < -cfg.NEUTRAL_SCORE) & ~two, "trailing", labels)
    return np.where(two, "two_minute", labels)


def _draw(rng: np.random.Generator, bank: np.ndarray, n: int) -> np.ndarray:
    if bank is None or len(bank) == 0:
        return np.full(n, 75.0)
    return bank[rng.integers(0, len(bank), n)]


def _pace(card: dict, offense_home: np.ndarray, state: np.ndarray, half_left: np.ndarray, rng, n: int):
    if card.get("fixed_plays") is not None:
        plays = np.full(n, float(card["fixed_plays"]))
        sec = plays * float(card["fixed_sec"])
    elif card.get("oracle"):
        plays = np.where(offense_home, card["oracle_home_plays"], card["oracle_away_plays"])
        sec = plays * np.where(offense_home, card["oracle_home_sec"], card["oracle_away_sec"])
    else:
        plays = np.zeros(n)
        sec = np.zeros(n)
        for name in cfg.SITUATIONS:
            mask = state == name
            if not np.any(mask):
                continue
            mu_h = card["home_pace"][name]["plays"]
            mu_a = card["away_pace"][name]["plays"]
            sec_h = card["home_pace"][name]["sec"]
            sec_a = card["away_pace"][name]["sec"]
            mu = np.where(offense_home, mu_h, mu_a)
            drawn = rng.poisson(np.maximum(mu, 0.5))
            plays = np.where(mask, np.clip(drawn, 1, cfg.MAX_PLAYS_PER_DRIVE), plays)
            sec = np.where(mask, plays * np.where(offense_home, sec_h, sec_a), sec)
    sec = np.minimum(sec, np.maximum(half_left, 0))
    return plays, sec


def _outcomes(card, n, rng, offense_home, yard, diff, half_left, home_to, away_to, active):
    if card.get("forced_outcome") is not None:
        return np.full(n, int(card["forced_outcome"]))
    model = card["drive_model"]
    ypp_on = bool(card.get("ypp_layer", cfg.YPP_LAYER))
    names = model["names"]
    index = {name: i + 1 for i, name in enumerate(names)}
    features = np.zeros((n, 1 + len(names)))
    features[:, 0] = 1.0

    def put(name: str, values: np.ndarray) -> None:
        slot = index.get(name)
        if slot is not None:
            features[:, slot] = values

    put("yardline_100", yard)
    put("score_diff", diff)
    put("half_seconds", half_left)
    put("timeouts", np.where(offense_home, home_to + away_to, away_to + home_to))
    put("fg_range", (yard <= cfg.FG_RANGE_YARDLINE).astype(float))
    put("red_zone", (yard <= cfg.RED_ZONE_YARDLINE).astype(float))
    put("two_minute", (half_left <= cfg.TWO_MINUTE_SECONDS).astype(float))
    put("trailing", (diff < -cfg.NEUTRAL_SCORE).astype(float))
    put("home", offense_home.astype(float))
    put("dome", np.full(n, float(card.get("dome", 0.0))))
    home_ctx = card.get("home_context", {})
    away_ctx = card.get("away_context", {})
    for name in names:
        if name in {"yardline_100", "score_diff", "half_seconds", "timeouts", "fg_range", "red_zone", "two_minute", "trailing", "home", "dome"}:
            continue
        if name in {"ypp_pass", "ypp_run"} and not ypp_on:
            put(name, np.zeros(n))
            continue
        home_value = float(home_ctx.get(name, 0.0))
        away_value = float(away_ctx.get(name, 0.0))
        put(name, np.where(offense_home, home_value, away_value))
    features = np.where(active[:, None], features, 0.0)
    probs = outcome_probabilities(features, model)
    draw = rng.random(n)
    cdf = np.cumsum(probs, axis=1)
    return (draw[:, None] > cdf).sum(axis=1).astype(int)


def _points(card, outcome, n, rng):
    off = np.zeros(n)
    deff = np.zeros(n)
    pat = rng.random(n) < card.get("pat_rate", 0.94)
    off = np.where(outcome == TD, np.where(pat, 7.0, 6.0), off)
    off = np.where(outcome == FG, 3.0, off)
    deff = np.where(outcome == SAFETY, 2.0, deff)
    punt_ret = (outcome == PUNT) & (rng.random(n) < card.get("p_punt_return_td", 0.0))
    to_ret = (outcome == TURNOVER) & (rng.random(n) < card.get("p_to_return_td", 0.0))
    deff = np.where(punt_ret | to_ret, deff + 7.0, deff)
    return off, deff


def simulate_game(card: dict, n: int, rng: np.random.Generator) -> dict[str, np.ndarray]:
    """Run n independent games. Clock and possession are tracked per sim."""
    home_pts = np.zeros(n)
    away_pts = np.zeros(n)
    home_plays = np.zeros(n)
    away_plays = np.zeros(n)
    home_drives = np.zeros(n)
    away_drives = np.zeros(n)
    ot_flag = np.zeros(n, dtype=bool)
    half_left = np.full(n, cfg.REGULATION_HALF_SECONDS)
    half_id = np.ones(n, dtype=int)
    if "receive_home" in card:
        receive_home0 = np.full(n, bool(card["receive_home"]))
    else:
        receive_home0 = rng.random(n) < 0.5
    offense_home = receive_home0.copy()
    yard = _draw(rng, card.get("banks", {}).get("KICKOFF", np.array([75.0])), n)
    home_to = np.full(n, 3.0)
    away_to = np.full(n, 3.0)
    alive = np.ones(n, dtype=bool)
    ot_poss = np.zeros(n, dtype=int)
    trace = []
    for _step in range(int(card.get("max_drives", cfg.MAX_DRIVES))):
        if not np.any(alive):
            break
        to_second = alive & (half_left <= 0) & (half_id == 1)
        if np.any(to_second):
            half_id = np.where(to_second, 2, half_id)
            half_left = np.where(to_second, cfg.REGULATION_HALF_SECONDS, half_left)
            offense_home = np.where(to_second, ~receive_home0, offense_home)
            yard = np.where(to_second, _draw(rng, card.get("banks", {}).get("KICKOFF", np.array([75.0])), n), yard)
            home_to = np.where(to_second, 3.0, home_to)
            away_to = np.where(to_second, 3.0, away_to)
        reg_done = alive & (half_left <= 0) & (half_id == 2)
        if np.any(reg_done):
            tied = reg_done & np.isclose(home_pts, away_pts)
            ot_flag = ot_flag | tied
            alive = (alive & ~reg_done) | tied
            half_id = np.where(tied, 3, half_id)
            half_left = np.where(tied, cfg.OT_SECONDS, half_left)
            coin = rng.random(n) < 0.5
            offense_home = np.where(tied, coin, offense_home)
            ot_poss = np.where(tied, 0, ot_poss)
        ot_done = alive & (half_left <= 0) & (half_id >= 3)
        alive = alive & ~ot_done
        active = alive & (half_left > 0)
        if not np.any(active):
            break
        trace.append(half_left.copy())
        diff = np.where(offense_home, home_pts - away_pts, away_pts - home_pts)
        state = situation_array(diff, half_left)
        plays, sec = _pace(card, offense_home, state, half_left, rng, n)
        outcome = _outcomes(
            card, n, rng, offense_home, yard, diff, half_left, home_to, away_to, active,
        )
        off_pts, def_pts = _points(card, outcome, n, rng)
        off_pts = np.where(active, off_pts, 0.0)
        def_pts = np.where(active, def_pts, 0.0)
        plays = np.where(active, plays, 0.0)
        sec = np.where(active, sec, 0.0)
        home_pts = home_pts + np.where(offense_home, off_pts, def_pts)
        away_pts = away_pts + np.where(offense_home, def_pts, off_pts)
        home_plays = home_plays + np.where(offense_home, plays, 0.0)
        away_plays = away_plays + np.where(~offense_home, plays, 0.0)
        home_drives = home_drives + np.where(offense_home & active, 1.0, 0.0)
        away_drives = away_drives + np.where(~offense_home & active, 1.0, 0.0)
        half_left = np.maximum(0.0, half_left - sec)
        first_td = (half_id >= 3) & active & (ot_poss == 0) & (outcome == TD)
        ot_poss = ot_poss + np.where((half_id >= 3) & active, 1, 0)
        alive = alive & ~first_td
        yard = _next_yard(card, outcome, n, rng)
        offense_home = np.where(active, ~offense_home, offense_home)
    return {
        "home_points": home_pts,
        "away_points": away_pts,
        "margin": home_pts - away_pts,
        "total": home_pts + away_pts,
        "home_plays": home_plays,
        "away_plays": away_plays,
        "home_drives": home_drives,
        "away_drives": away_drives,
        "ot": ot_flag.astype(float),
        "half_trace": trace,
    }


def _next_yard(card, outcome, n, rng):
    banks = card.get("banks", {})
    yard = np.full(n, 75.0)
    for index, label in enumerate(cfg.DRIVE_OUTCOMES):
        mask = outcome == index
        if not np.any(mask):
            continue
        bank = banks.get(label, banks.get("KICKOFF", np.array([75.0])))
        yard = np.where(mask, _draw(rng, bank, n), yard)
    return yard


def summarize_sims(result: dict[str, np.ndarray]) -> dict[str, float]:
    """Mean, sd, and the frozen quantiles for one game's simulations."""
    out = {"p_home_win": float(np.mean(result["margin"] > 0))}
    fields = {
        "margin": result["margin"],
        "total": result["total"],
        "home_points": result["home_points"],
        "away_points": result["away_points"],
        "home_plays": result["home_plays"],
        "away_plays": result["away_plays"],
        "plays": result["home_plays"] + result["away_plays"],
        "home_drives": result["home_drives"],
        "away_drives": result["away_drives"],
        "drives": result["home_drives"] + result["away_drives"],
    }
    for name, values in fields.items():
        out[f"{name}_mean"] = float(np.mean(values))
        out[f"{name}_sd"] = float(np.std(values))
        for q in cfg.QUANTILES:
            out[f"{name}_p{int(q * 100):02d}"] = float(np.quantile(values, q))
    out["ot_rate"] = float(np.mean(result["ot"]))
    return out
