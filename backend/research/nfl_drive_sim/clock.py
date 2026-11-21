"""Game-clock runoff by play class.

Seconds are the drop in ``game_seconds_remaining`` until the next play.
A gap above the configured cap is a quarter break or a missing row, not one
play's runoff, so it is excluded from the mean. Incompletions, spikes, and
out-of-bounds plays keep their own means: those are the plays that stop the
clock. Kickoffs, punts, field goals, and extra points are not scrimmage
plays and are not charged a scrimmage interval.

The class list was chosen by accounting a full training-season clock back to
60 minutes. The means themselves are re-estimated from games that have
already kicked off.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import sim_config as cfg
from .diagnostics import play_clock_deltas

CLASSES = (
    "pass_complete",
    "pass_incomplete",
    "pass_oob",
    "run",
    "run_oob",
    "sack",
    "spike",
    "kneel",
    "punt",
    "kickoff",
    "field_goal",
    "extra_point",
    "no_play",
)

# Used only when a training cell is empty. These are round training-season
# runoff values, not a fit to the 2018 scores.
FALLBACK = {
    "pass_complete": 30.0,
    "pass_incomplete": 10.0,
    "pass_oob": 8.0,
    "run": 34.0,
    "run_oob": 12.0,
    "sack": 12.0,
    "spike": 2.0,
    "kneel": 26.0,
    "punt": 10.0,
    "kickoff": 3.0,
    "field_goal": 4.0,
    "extra_point": 0.0,
    "no_play": 8.0,
}

RUNNING = ("pass_complete", "run", "kneel")
SCRIMMAGE_CLASSES = ("pass_complete", "pass_incomplete", "pass_oob", "run", "run_oob", "sack", "spike", "kneel")


def classify_plays(frame: pd.DataFrame) -> pd.Series:
    play_type = frame["play_type"].astype(str) if "play_type" in frame.columns else pd.Series("", index=frame.index)
    sack = _flag(frame, "sack")
    complete = _flag(frame, "complete_pass")
    oob = _flag(frame, "out_of_bounds") if "out_of_bounds" in frame.columns else pd.Series(False, index=frame.index)
    labels = np.full(len(frame), "other", dtype=object)
    labels = np.where(play_type.eq("no_play"), "no_play", labels)
    labels = np.where(play_type.eq("kickoff"), "kickoff", labels)
    labels = np.where(play_type.eq("punt"), "punt", labels)
    labels = np.where(play_type.eq("field_goal"), "field_goal", labels)
    labels = np.where(play_type.eq("extra_point"), "extra_point", labels)
    labels = np.where(play_type.eq("qb_spike"), "spike", labels)
    labels = np.where(play_type.eq("qb_kneel"), "kneel", labels)
    labels = np.where(play_type.eq("run") & oob, "run_oob", labels)
    labels = np.where(play_type.eq("run") & ~oob, "run", labels)
    labels = np.where(play_type.eq("pass") & sack, "sack", labels)
    labels = np.where(play_type.eq("pass") & ~sack & ~complete, "pass_incomplete", labels)
    labels = np.where(play_type.eq("pass") & ~sack & complete & oob, "pass_oob", labels)
    labels = np.where(play_type.eq("pass") & ~sack & complete & ~oob, "pass_complete", labels)
    return pd.Series(labels, index=frame.index)


def _flag(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(False, index=frame.index)
    return pd.to_numeric(frame[column], errors="coerce").fillna(0).eq(1)


def situation_labels(frame: pd.DataFrame) -> pd.Series:
    score = pd.to_numeric(frame.get("score_differential"), errors="coerce").fillna(0)
    half_left = pd.to_numeric(frame.get("half_seconds_remaining"), errors="coerce").fillna(1800)
    two = half_left <= cfg.TWO_MINUTE_SECONDS
    labels = np.where(
        two, "two_minute",
        np.where(score > cfg.NEUTRAL_SCORE, "leading", np.where(score < -cfg.NEUTRAL_SCORE, "trailing", "neutral")),
    )
    return pd.Series(labels, index=frame.index)


def fit_clock(plays: pd.DataFrame) -> dict:
    """Mean runoff by class and situation, plus the mix used to draw play types."""
    if plays is None or len(plays) == 0:
        return _empty_clock()
    timed = play_clock_deltas(plays)
    timed["clock_class"] = classify_plays(timed)
    timed["situation"] = situation_labels(timed)
    means = {}
    for play_class in CLASSES:
        for state in cfg.SITUATIONS:
            part = timed.loc[timed["clock_class"].eq(play_class) & timed["situation"].eq(state), "clock_delta"]
            means[(play_class, state)] = float(part.mean()) if len(part) else FALLBACK[play_class]
    mix = {}
    for state in cfg.SITUATIONS:
        part = timed.loc[timed["situation"].eq(state) & timed["clock_class"].isin(SCRIMMAGE_CLASSES)]
        n = max(len(part), 1)
        mix[state] = {
            "pass_rate": float(part["clock_class"].isin(["pass_complete", "pass_incomplete", "pass_oob", "sack"]).mean()) if len(part) else 0.55,
            "sack_rate": float(part["clock_class"].eq("sack").sum() / max(part["clock_class"].isin(["pass_complete", "pass_incomplete", "pass_oob", "sack"]).sum(), 1)),
            "inc_rate": float(part["clock_class"].eq("pass_incomplete").sum() / max(part["clock_class"].isin(["pass_complete", "pass_incomplete", "pass_oob"]).sum(), 1)),
            "comp_oob": float(part["clock_class"].eq("pass_oob").sum() / max(part["clock_class"].isin(["pass_complete", "pass_oob"]).sum(), 1)),
            "run_oob": float(part["clock_class"].eq("run_oob").sum() / max(part["clock_class"].isin(["run", "run_oob"]).sum(), 1)),
            "n": n,
            "penalty_per_play": float(
                timed.loc[timed["situation"].eq(state) & timed["clock_class"].eq("no_play"), "clock_delta"].sum()
            ) / max(len(part), 1),
        }
    rates = timed.loc[timed["clock_class"].isin(SCRIMMAGE_CLASSES)].groupby(
        ["game_id", "posteam"], sort=False,
    )["clock_delta"].mean()
    within = rates.groupby(level=1).std(ddof=0)
    shock = float(within.mean()) if len(within) else 0.0
    if not np.isfinite(shock):
        shock = 0.0
    return {"means": means, "mix": mix, "sec_shock_sd": shock}


def _empty_clock() -> dict:
    means = {(play_class, state): FALLBACK[play_class] for play_class in CLASSES for state in cfg.SITUATIONS}
    mix = {
        state: {
            "pass_rate": 0.55, "sack_rate": 0.06, "inc_rate": 0.35,
            "comp_oob": 0.08, "run_oob": 0.12, "penalty_per_play": 1.0, "n": 0,
        }
        for state in cfg.SITUATIONS
    }
    return {"means": means, "mix": mix}


def lookup(clock: dict, play_class: str, state: str) -> float:
    value = clock["means"].get((play_class, state), FALLBACK[play_class])
    if not np.isfinite(value):
        return FALLBACK[play_class]
    return float(value)


def fit_scrimmage_lengths(drives: pd.DataFrame, plays: pd.DataFrame) -> dict:
    """Mean scrimmage plays by outcome and the drive's starting situation."""
    lengths = {}
    league = {state: 5.5 for state in cfg.SITUATIONS}
    if drives is None or len(drives) == 0:
        for outcome in cfg.DRIVE_OUTCOMES:
            for state in cfg.SITUATIONS:
                lengths[(outcome, state)] = league[state]
        return {"length": lengths, "league_ppd": league, "phi": 1.0}
    frame = drives.copy()
    frame["game_id"] = frame["game_id"].astype(str)
    frame["fixed_drive"] = pd.to_numeric(frame["drive"], errors="coerce")
    if plays is not None and len(plays):
        timed = play_clock_deltas(plays)
        scrimmage = timed["play_type"].isin(["pass", "run", "qb_kneel", "qb_spike"])
        counts = (
            timed.loc[scrimmage]
            .groupby(["game_id", "fixed_drive"], sort=False)
            .size()
            .rename("scrimmage_plays")
            .reset_index()
        )
        counts["game_id"] = counts["game_id"].astype(str)
        frame = frame.merge(counts, on=["game_id", "fixed_drive"], how="left")
    else:
        frame["scrimmage_plays"] = pd.to_numeric(frame["plays"], errors="coerce")
    frame["scrimmage_plays"] = frame["scrimmage_plays"].fillna(0.0)
    score = pd.to_numeric(frame["start_score_diff"], errors="coerce").fillna(0)
    half_left = pd.to_numeric(frame["start_half_seconds_remaining"], errors="coerce").fillna(1800)
    two = half_left <= cfg.TWO_MINUTE_SECONDS
    frame["situation"] = np.where(
        two, "two_minute",
        np.where(score > cfg.NEUTRAL_SCORE, "leading", np.where(score < -cfg.NEUTRAL_SCORE, "trailing", "neutral")),
    )
    for state in cfg.SITUATIONS:
        part = frame.loc[frame["situation"].eq(state)]
        league[state] = float(part["scrimmage_plays"].mean()) if len(part) else 5.5
        for outcome in cfg.DRIVE_OUTCOMES:
            cell = part.loc[part["outcome"].eq(outcome), "scrimmage_plays"]
            lengths[(outcome, state)] = float(cell.mean()) if len(cell) else league[state]
    mean = float(frame["scrimmage_plays"].mean()) if len(frame) else 0.0
    var = float(frame["scrimmage_plays"].var(ddof=0)) if len(frame) else 0.0
    phi = float(var / mean) if mean > 0 else 1.0
    return {"length": lengths, "league_ppd": league, "phi": max(phi, 1.0)}


def _shrink(counts: np.ndarray, parent: np.ndarray, k: float) -> np.ndarray:
    """Dirichlet shrinkage of outcome counts toward a coarser probability."""
    n = counts.sum(axis=-1, keepdims=True)
    return (counts + k * parent) / np.maximum(n + k, 1e-9)


def fit_late_table(drives: pd.DataFrame) -> dict:
    """How a two-minute drive ends, given the score and the field position.

    The table is score bin x yardline bin x time bin x timeout bin.
    Each cell shrinks toward the next coarser table: drop timeouts, then
    time, then yardline, then the two-minute total. Fit only on drives
    already in ``drives``.
    """
    yard_edges = np.asarray(cfg.LATE_YARD_EDGES, dtype=float)
    score_edges = np.asarray(cfg.LATE_SCORE_EDGES, dtype=float)
    n_score = len(score_edges) - 1
    n_yard = len(yard_edges) - 1
    n_time = 2
    n_timeout = 2
    n_out = len(cfg.DRIVE_OUTCOMES)
    shape = (n_score, n_yard, n_time, n_timeout, n_out)
    counts = np.zeros(shape)
    labels = {name: index for index, name in enumerate(cfg.DRIVE_OUTCOMES)}
    if drives is not None and len(drives):
        half = pd.to_numeric(drives["start_half_seconds_remaining"], errors="coerce").fillna(1800)
        late = drives.loc[half.to_numpy() <= cfg.TWO_MINUTE_SECONDS].copy()
        late = late[late["outcome"].isin(cfg.DRIVE_OUTCOMES)]
        if len(late):
            diff = pd.to_numeric(late["start_score_diff"], errors="coerce").fillna(0).to_numpy()
            yards = pd.to_numeric(late["start_yardline_100"], errors="coerce").fillna(75).to_numpy()
            left = pd.to_numeric(late["start_half_seconds_remaining"], errors="coerce").fillna(60).to_numpy()
            timeouts = pd.to_numeric(late.get("start_posteam_timeouts"), errors="coerce").fillna(3).to_numpy()
            sbin = np.clip(np.searchsorted(score_edges, diff, side="right") - 1, 0, n_score - 1)
            ybin = np.clip(np.searchsorted(yard_edges, yards, side="right") - 1, 0, n_yard - 1)
            tbin = (left > cfg.LATE_TIME_SPLIT).astype(int)
            obin = (timeouts >= 1).astype(int)
            outcomes = late["outcome"].map(labels).to_numpy(dtype=int)
            for a, b, c, d, e in zip(sbin, ybin, tbin, obin, outcomes, strict=False):
                counts[int(a), int(b), int(c), int(d), int(e)] += 1.0
    if counts.sum() == 0:
        counts[..., cfg.DRIVE_OUTCOMES.index("END_OF_HALF")] = 1.0
    k = float(cfg.LATE_SHRINK_K)
    overall = counts.sum(axis=(0, 1, 2, 3))
    p_overall = overall / np.maximum(overall.sum(), 1e-9)
    p_score = _shrink(counts.sum(axis=(1, 2, 3)), p_overall, k)
    p_yard = _shrink(counts.sum(axis=(2, 3)), p_score[:, None, :], k)
    p_time = _shrink(counts.sum(axis=3), p_yard[:, :, None, :], k)
    probs = _shrink(counts, p_time[:, :, :, None, :], k)
    return {
        "yard_edges": yard_edges,
        "score_edges": score_edges,
        "probs": probs,
    }
