"""Vectorized game sim.

2018 overtime. Each period is 10 minutes. A touchdown on the first
possession wins immediately, and so does a safety or a defensive score.
A field goal on that first possession does not end the game. Once both
teams have possessed, the next score wins. If the clock expires with the
score tied, a regular-season game stays tied. A playoff starts another
period and keeps going. Possessions are an output of the clock, not an
input, unless an oracle pace is supplied.
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


FG_MISSED = cfg.DRIVE_OUTCOMES.index("FG_MISSED")
KICKOFF_OUTCOMES = (TD, FG, SAFETY)


def _pace(card: dict, offense_home: np.ndarray, state: np.ndarray, half_left: np.ndarray, rng, n: int,
          outcome: np.ndarray | None = None, kickoff: np.ndarray | None = None):
    if card.get("fixed_plays") is not None:
        plays = np.full(n, float(card["fixed_plays"]))
        sec = plays * float(card["fixed_sec"])
    elif card.get("clock") is not None and outcome is not None:
        drawn, body, start, terminal = _class_clock(
            card, offense_home, state, outcome, kickoff, rng, n,
        )
        plays, sec, scrimmage, outcome = _apply_truncation(
            drawn, body, start, terminal, half_left, outcome, rng,
        )
        return plays, sec, scrimmage, outcome
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
    full = sec
    sec = np.minimum(full, np.maximum(half_left, 0.0))
    ratio = np.ones(n)
    np.divide(sec, np.maximum(full, 1e-6), out=ratio, where=full > 1e-6)
    plays = plays * np.clip(ratio, 0.0, 1.0)
    return plays, sec, sec, outcome


def _class_clock(card, offense_home, state, outcome, kickoff, rng, n):
    """Scrimmage plays from the outcome's length, clock from the play class."""
    from .clock import lookup

    clock = card["clock"]
    lengths = card["lengths"]
    state_code = np.zeros(n, dtype=int)
    pred_plays = np.zeros(n)
    pred_sec = np.zeros(n)
    pen_rate = np.zeros(n)
    league = np.zeros(n)
    base = np.zeros(n)
    kick_sec = np.zeros(n)
    punt_sec = np.zeros(n)
    fg_sec = np.zeros(n)
    xp_sec = np.zeros(n)
    for s_index, sname in enumerate(cfg.SITUATIONS):
        smask = state == sname
        if not np.any(smask):
            continue
        state_code = np.where(smask, s_index, state_code)
        plays_s = np.where(
            offense_home, card["home_pace"][sname]["plays"], card["away_pace"][sname]["plays"],
        )
        sec_s = np.where(
            offense_home, card["home_pace"][sname]["sec"], card["away_pace"][sname]["sec"],
        )
        if card.get("oracle"):
            plays_s = np.where(offense_home, card["oracle_home_plays"], card["oracle_away_plays"])
            sec_s = np.where(offense_home, card["oracle_home_sec"], card["oracle_away_sec"])
        pred_plays = np.where(smask, plays_s, pred_plays)
        pred_sec = np.where(smask, sec_s, pred_sec)
        pen_rate = np.where(smask, float(clock["mix"][sname].get("penalty_per_play", 0.0)), pen_rate)
        league_s = float(lengths["league_ppd"].get(sname, 5.5))
        league = np.where(smask, league_s, league)
        cell = np.array([
            float(lengths["length"].get((oname, sname), league_s)) for oname in cfg.DRIVE_OUTCOMES
        ])
        base = np.where(smask, cell[outcome], base)
        kick_sec = np.where(smask, lookup(clock, "kickoff", sname), kick_sec)
        punt_sec = np.where(smask, lookup(clock, "punt", sname), punt_sec)
        fg_sec = np.where(smask, lookup(clock, "field_goal", sname), fg_sec)
        xp_sec = np.where(smask, lookup(clock, "extra_point", sname), xp_sec)
    if card.get("oracle"):
        # Keep a punt shorter than a scoring drive. Scale the training lengths
        # so their level matches this game's actual scrimmage plays per drive.
        scale = pred_plays / np.maximum(league, 1e-6)
        mu = np.clip(base * scale, 1.0, cfg.MAX_PLAYS_PER_DRIVE)
    else:
        mu = np.clip(base + (pred_plays - league), 1.0, cfg.MAX_PLAYS_PER_DRIVE)
    if card.get("oracle"):
        # The oracle is the actual mean pace, not a new noisy draw around it.
        drawn = np.clip(np.rint(mu), 1, cfg.MAX_PLAYS_PER_DRIVE).astype(int)
    else:
        phi = float(lengths.get("phi", 1.0))
        if phi <= 1.01:
            drawn = rng.poisson(mu)
        else:
            n_succ = np.maximum(mu / (phi - 1.0), 1e-3)
            drawn = rng.negative_binomial(n_succ, 1.0 / phi)
        drawn = np.clip(drawn, 1, cfg.MAX_PLAYS_PER_DRIVE).astype(int)
    # One mixture for the whole array. Class means vary by state, so apply per state.
    sec = np.zeros(n)
    for s_index, sname in enumerate(cfg.SITUATIONS):
        smask = state_code == s_index
        if not np.any(smask):
            continue
        mix = clock["mix"][sname]
        pass_rate = np.where(offense_home, card.get("home_pass", mix["pass_rate"]), card.get("away_pass", mix["pass_rate"]))
        inc_rate = np.where(offense_home, card.get("home_inc", mix["inc_rate"]), card.get("away_inc", mix["inc_rate"]))
        sack_rate = np.where(offense_home, card.get("home_sack", mix["sack_rate"]), card.get("away_sack", mix["sack_rate"]))
        shocked = pred_sec
        if card.get("home_sec_shock") is not None and not card.get("oracle"):
            shocked = pred_sec + np.where(offense_home, card["home_sec_shock"], card["away_sec_shock"])
            shocked = np.clip(shocked, 8.0, cfg.SEC_CAP)
        part = _scrimmage_seconds(
            rng, drawn, pass_rate, inc_rate, sack_rate, mix, clock, sname, shocked,
        )
        sec = np.where(smask, part, sec)
    kick = kickoff if kickoff is not None else np.zeros(n, dtype=bool)
    start = np.where(kick, kick_sec, 0.0)
    terminal = np.zeros(n)
    terminal = np.where(outcome == PUNT, punt_sec, terminal)
    terminal = np.where((outcome == FG) | (outcome == FG_MISSED), fg_sec, terminal)
    terminal = np.where(outcome == TD, xp_sec, terminal)
    terminal = terminal + drawn.astype(float) * pen_rate
    return drawn.astype(float), sec, start, terminal


def _apply_truncation(drawn, body, special_start, terminal, half_left, outcome, rng):
    """Drop plays that do not fit, and drop the ending if the clock wins.

    ``half_left`` may be NaN when the caller still has to pass the live clock.
    A finite clock caps the drive. The sampled ending is kept only when it
    falls in the part of the drive that actually ran.
    """
    body = np.asarray(body, dtype=float)
    drawn = np.asarray(drawn, dtype=float)
    if half_left is None or np.all(~np.isfinite(half_left)):
        return drawn, body + special_start + terminal, body, outcome
    half_left = np.asarray(half_left, dtype=float)
    remain = np.maximum(0.0, half_left - special_start)
    ratio = np.ones(drawn.shape, dtype=float)
    np.divide(remain, np.maximum(body, 1e-6), out=ratio, where=body > 1e-6)
    ratio = np.clip(np.where(np.isfinite(half_left), ratio, 1.0), 0.0, 1.0)
    plays = drawn * ratio
    scrimmage = body * ratio
    # The sampled ending is kept. Two-minute endings already come from the
    # training frequencies, which include clock expiration. Only the snaps
    # that fit are counted.
    total = special_start + scrimmage + terminal
    new_outcome = outcome
    total = np.minimum(total, np.where(np.isfinite(half_left), np.maximum(half_left, 0.0), total))
    return plays, total, scrimmage, new_outcome


def _scrimmage_seconds(rng, n_plays, pass_rate, inc_rate, sack_rate, mix, clock, state, pred_sec):
    from .clock import lookup

    n_plays = n_plays.astype(int)
    n_pass = rng.binomial(n_plays, np.clip(pass_rate, 0.0, 1.0))
    n_sack = rng.binomial(n_pass, np.clip(sack_rate, 0.0, 1.0))
    n_throw = n_pass - n_sack
    n_inc = rng.binomial(n_throw, np.clip(inc_rate, 0.0, 1.0))
    n_caught = n_throw - n_inc
    n_comp_oob = rng.binomial(n_caught, np.clip(mix.get("comp_oob", 0.0), 0.0, 1.0))
    n_comp = n_caught - n_comp_oob
    n_run_all = n_plays - n_pass
    n_run_oob = rng.binomial(n_run_all, np.clip(mix.get("run_oob", 0.0), 0.0, 1.0))
    n_run = n_run_all - n_run_oob
    means = {name: lookup(clock, name, state) for name in (
        "pass_complete", "pass_incomplete", "pass_oob", "run", "run_oob", "sack",
    )}
    base = _expected_scrimmage_seconds(pass_rate, inc_rate, sack_rate, mix, means)
    weight = (
        np.clip(pass_rate, 0, 1) * (1 - np.clip(sack_rate, 0, 1)) * (1 - np.clip(inc_rate, 0, 1))
        * (1 - np.clip(mix.get("comp_oob", 0.0), 0, 1))
        + (1 - np.clip(pass_rate, 0, 1)) * (1 - np.clip(mix.get("run_oob", 0.0), 0, 1))
    )
    pred = np.where(np.isfinite(pred_sec), pred_sec, base)
    shift = np.zeros(np.shape(weight), dtype=float)
    np.divide(pred - base, weight, out=shift, where=weight > 0.05)
    sec_comp = np.clip(means["pass_complete"] + shift, 0.0, cfg.SEC_CAP)
    sec_run = np.clip(means["run"] + shift, 0.0, cfg.SEC_CAP)
    return (
        n_inc * means["pass_incomplete"]
        + n_comp * sec_comp
        + n_comp_oob * means["pass_oob"]
        + n_run * sec_run
        + n_run_oob * means["run_oob"]
        + n_sack * means["sack"]
    )


def _expected_scrimmage_seconds(pass_rate, inc_rate, sack_rate, mix, means) -> np.ndarray:
    p = np.clip(pass_rate, 0.0, 1.0)
    sack = np.clip(sack_rate, 0.0, 1.0)
    inc = np.clip(inc_rate, 0.0, 1.0)
    comp_oob = np.clip(mix.get("comp_oob", 0.0), 0.0, 1.0)
    run_oob = np.clip(mix.get("run_oob", 0.0), 0.0, 1.0)
    throw = p * (1 - sack)
    run = 1 - p
    return (
        p * sack * means["sack"]
        + throw * inc * means["pass_incomplete"]
        + throw * (1 - inc) * comp_oob * means["pass_oob"]
        + throw * (1 - inc) * (1 - comp_oob) * means["pass_complete"]
        + run * run_oob * means["run_oob"]
        + run * (1 - run_oob) * means["run"]
    )


def _outcomes(card, n, rng, offense_home, yard, diff, half_left, home_to, away_to, active):
    script = card.get("scripted_outcomes")
    if script:
        index = int(card.get("_script_i", 0))
        card["_script_i"] = index + 1
        return np.full(n, int(script[min(index, len(script) - 1)]))
    if card.get("forced_outcome") is not None:
        return np.full(n, int(card["forced_outcome"]))
    late = card.get("late_table")
    if late is not None and np.any(half_left <= cfg.TWO_MINUTE_SECONDS):
        timeouts = np.where(offense_home, home_to, away_to)
        late_draw = _sample_late(yard, diff, half_left, timeouts, rng, late)
    else:
        late_draw = None
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
    chosen = (draw[:, None] > cdf).sum(axis=1).astype(int)
    if late_draw is not None:
        chosen = np.where(half_left <= cfg.TWO_MINUTE_SECONDS, late_draw, chosen)
    return chosen


def _sample_late(yard, diff, half_left, timeouts, rng, table: dict) -> np.ndarray:
    """Draw a two-minute ending from the score x field x time x timeout table."""
    score_edges = np.asarray(table["score_edges"], dtype=float)
    yard_edges = np.asarray(table["yard_edges"], dtype=float)
    probs = np.asarray(table["probs"], dtype=float)
    sbin = np.clip(np.searchsorted(score_edges, diff, side="right") - 1, 0, probs.shape[0] - 1)
    ybin = np.clip(np.searchsorted(yard_edges, yard, side="right") - 1, 0, probs.shape[1] - 1)
    tbin = (half_left > cfg.LATE_TIME_SPLIT).astype(int)
    obin = (timeouts >= 1).astype(int)
    chosen = probs[sbin, ybin, tbin, obin]
    cdf = np.cumsum(chosen, axis=1)
    draw = rng.random(yard.shape[0])
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
    home_scr_sec = np.zeros(n)
    away_scr_sec = np.zeros(n)
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
    starts_with_kick = np.ones(n, dtype=bool)
    yard = _draw(rng, card.get("banks", {}).get("KICKOFF", np.array([75.0])), n)
    home_to = np.full(n, 3.0)
    away_to = np.full(n, 3.0)
    shock_sd = float(card.get("clock", {}).get("sec_shock_sd") or 0.0) if card.get("clock") else 0.0
    if shock_sd > 0:
        card["home_sec_shock"] = rng.normal(0.0, shock_sd, n)
        card["away_sec_shock"] = rng.normal(0.0, shock_sd, n)
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
            starts_with_kick = np.where(to_second, True, starts_with_kick)
        reg_done = alive & (half_left <= 0) & (half_id == 2)
        if np.any(reg_done):
            tied = reg_done & np.isclose(home_pts, away_pts)
            ot_flag = ot_flag | tied
            alive = (alive & ~reg_done) | tied
            half_id = np.where(tied, 3, half_id)
            half_left = np.where(tied, cfg.OT_SECONDS, half_left)
            if card.get("ot_receive_home") is None:
                coin = rng.random(n) < 0.5
            else:
                coin = np.full(n, bool(card["ot_receive_home"]))
            offense_home = np.where(tied, coin, offense_home)
            ot_poss = np.where(tied, 0, ot_poss)
            starts_with_kick = np.where(tied, True, starts_with_kick)
        ot_clock_out = alive & (half_left <= 0) & (half_id >= 3)
        if np.any(ot_clock_out):
            if card.get("playoff"):
                still_tied = ot_clock_out & np.isclose(home_pts, away_pts)
                half_id = np.where(still_tied, half_id + 1, half_id)
                half_left = np.where(still_tied, cfg.OT_SECONDS, half_left)
                starts_with_kick = np.where(still_tied, True, starts_with_kick)
                alive = (alive & ~ot_clock_out) | still_tied
            else:
                alive = alive & ~ot_clock_out
        active = alive & (half_left > 0)
        if not np.any(active):
            break
        trace.append(half_left.copy())
        diff = np.where(offense_home, home_pts - away_pts, away_pts - home_pts)
        state = situation_array(diff, half_left)
        outcome = _outcomes(
            card, n, rng, offense_home, yard, diff, half_left, home_to, away_to, active,
        )
        plays, sec, scrimmage_sec, outcome = _pace(
            card, offense_home, state, half_left, rng, n, outcome=outcome, kickoff=starts_with_kick,
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
        home_scr_sec = home_scr_sec + np.where(offense_home, scrimmage_sec, 0.0)
        away_scr_sec = away_scr_sec + np.where(~offense_home, scrimmage_sec, 0.0)
        home_drives = home_drives + np.where(offense_home & active, 1.0, 0.0)
        away_drives = away_drives + np.where(~offense_home & active, 1.0, 0.0)
        half_left = np.maximum(0.0, half_left - sec)
        _tally(
            card, active, state, outcome, plays, sec, off_pts + def_pts, yard, half_id, scrimmage_sec,
        )
        in_ot = (half_id >= 3) & active
        first_poss = in_ot & (ot_poss == 0)
        later_poss = in_ot & (ot_poss >= 1)
        first_ends = first_poss & ((outcome == TD) | (outcome == SAFETY) | (def_pts >= 6))
        later_ends = later_poss & ((off_pts + def_pts) > 0)
        alive = alive & ~first_ends & ~later_ends
        ot_poss = ot_poss + np.where(in_ot, 1, 0)
        yard = _next_yard(card, outcome, n, rng)
        scored_kick = np.isin(outcome, KICKOFF_OUTCOMES)
        starts_with_kick = np.where(active, scored_kick, starts_with_kick)
        offense_home = np.where(active, ~offense_home, offense_home)
    return {
        "home_points": home_pts,
        "away_points": away_pts,
        "margin": home_pts - away_pts,
        "total": home_pts + away_pts,
        "home_plays": home_plays,
        "away_plays": away_plays,
        "home_scr_sec": home_scr_sec,
        "away_scr_sec": away_scr_sec,
        "home_drives": home_drives,
        "away_drives": away_drives,
        "ot": ot_flag.astype(float),
        "half_trace": trace,
    }


def new_tally() -> dict[str, np.ndarray]:
    """Accumulators for one diagnostic pass. Sums across every simulated drive."""
    n_state = len(cfg.SITUATIONS)
    n_out = len(cfg.DRIVE_OUTCOMES)
    return {
        "n": np.zeros((n_state, n_out)),
        "plays": np.zeros((n_state, n_out)),
        "sec": np.zeros((n_state, n_out)),
        "scr_sec": np.zeros((n_state, n_out)),
        "points": np.zeros((n_state, n_out)),
        "yard": np.zeros((n_state, n_out)),
        "half": np.zeros(3),
    }


def _tally(card, active, state, outcome, plays, sec, points, yard, half_id, scrimmage_sec=None) -> None:
    bucket = card.get("diagnose")
    if bucket is None or not np.any(active):
        return
    codes = np.zeros(outcome.shape, dtype=int)
    for index, name in enumerate(cfg.SITUATIONS):
        codes = np.where(state == name, index, codes)
    flat = codes * len(cfg.DRIVE_OUTCOMES) + outcome.astype(int)
    weight = active.astype(float)
    counts = np.bincount(flat, weights=weight, minlength=bucket["n"].size)
    bucket["n"] += counts.reshape(bucket["n"].shape)
    fields = [("plays", plays), ("sec", sec), ("points", points), ("yard", yard)]
    if scrimmage_sec is not None and "scr_sec" in bucket:
        fields.append(("scr_sec", scrimmage_sec))
    for key, values in fields:
        summed = np.bincount(flat, weights=values * weight, minlength=bucket["n"].size)
        bucket[key] += summed.reshape(bucket["n"].shape)
    half_code = np.clip(half_id.astype(int), 1, 3) - 1
    bucket["half"] += np.bincount(half_code, weights=weight, minlength=3)[:3]


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
