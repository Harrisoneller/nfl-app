"""Sim-versus-actual drive mechanics on the 2018 dev season.

Actual rows use the play clock. A play's seconds are the drop in
game_seconds_remaining until the next play in that game, and a drop above
the configured cap is not treated as one play. Scrimmage plays are passes,
runs, kneels, and spikes. Special-teams plays stay on the drive they are
filed under, including the kickoff that starts it.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import sim_config as cfg

SCRIMMAGE = ("pass", "run", "qb_kneel", "qb_spike")


def play_clock_deltas(plays: pd.DataFrame) -> pd.DataFrame:
    frame = plays.sort_values(["game_id", "play_id"], kind="mergesort").copy()
    frame["game_id"] = frame["game_id"].astype(str)
    clock = pd.to_numeric(frame["game_seconds_remaining"], errors="coerce")
    nxt = clock.groupby(frame["game_id"]).shift(-1)
    delta = (clock - nxt).where(nxt.notna() & nxt.le(clock), 0.0)
    frame["clock_delta"] = delta.fillna(0.0).clip(lower=0.0, upper=cfg.SEC_CAP)
    return frame


def actual_diagnostic(drives: pd.DataFrame, plays: pd.DataFrame) -> pd.DataFrame:
    """One row per state for the real 2018 drives."""
    timed = play_clock_deltas(plays)
    timed["fixed_drive"] = pd.to_numeric(timed["fixed_drive"], errors="coerce")
    scrimmage = timed["play_type"].isin(SCRIMMAGE)
    keys = ["game_id", "fixed_drive"]
    seconds = timed.groupby(keys, sort=False)["clock_delta"].sum().rename("seconds").reset_index()
    scrimmage_plays = timed.loc[scrimmage].groupby(keys, sort=False).size().rename("scrimmage_plays").reset_index()
    scrimmage_seconds = (
        timed.loc[scrimmage].groupby(keys, sort=False)["clock_delta"].sum().rename("scrimmage_seconds").reset_index()
    )
    frame = drives.copy()
    frame["game_id"] = frame["game_id"].astype(str)
    frame["fixed_drive"] = pd.to_numeric(frame["drive"], errors="coerce")
    frame = frame.merge(seconds, on=keys, how="left")
    frame = frame.merge(scrimmage_plays, on=keys, how="left")
    frame = frame.merge(scrimmage_seconds, on=keys, how="left")
    frame["seconds"] = frame["seconds"].fillna(0.0)
    frame["scrimmage_plays"] = frame["scrimmage_plays"].fillna(0.0)
    frame["scrimmage_seconds"] = frame["scrimmage_seconds"].fillna(0.0)
    frame["points"] = (
        pd.to_numeric(frame["posteam_points"], errors="coerce").fillna(0)
        + pd.to_numeric(frame["defteam_points"], errors="coerce").fillna(0)
    )
    frame["yard"] = pd.to_numeric(frame["start_yardline_100"], errors="coerce")
    score = pd.to_numeric(frame["start_score_diff"], errors="coerce").fillna(0)
    half_left = pd.to_numeric(frame["start_half_seconds_remaining"], errors="coerce").fillna(1800)
    two = half_left <= cfg.TWO_MINUTE_SECONDS
    frame["situation"] = np.where(
        two, "two_minute",
        np.where(score > cfg.NEUTRAL_SCORE, "leading", np.where(score < -cfg.NEUTRAL_SCORE, "trailing", "neutral")),
    )
    qtr = pd.to_numeric(frame["start_qtr"], errors="coerce").fillna(1)
    frame["half_name"] = np.where(qtr <= 2, "half_1", np.where(qtr <= 4, "half_2", "ot"))
    return _summarize_drives(frame, "actual")


def sim_diagnostic(tally: dict[str, np.ndarray], *, n_games: int, n_sims: int) -> pd.DataFrame:
    """The same rows from simulated drives. Means are per simulated game."""
    scale = max(n_games * n_sims, 1)
    rows = []
    overall_n = tally["n"].sum()
    overall = {
        "plays": float(tally["plays"].sum()),
        "sec": float(tally["sec"].sum()),
        "points": float(tally["points"].sum()),
        "yard_sum": float(tally["yard"].sum()),
        "n": float(overall_n),
        "shares": tally["n"].sum(axis=0),
        "scrimmage_seconds": float(tally["sec"].sum()),
    }
    rows.extend(_metric_rows("sim", "overall", overall, tally["half"] / scale, scale))
    for index, state in enumerate(cfg.SITUATIONS):
        block = {
            "plays": float(tally["plays"][index].sum()),
            "sec": float(tally["sec"][index].sum()),
            "points": float(tally["points"][index].sum()),
            "yard_sum": float(tally["yard"][index].sum()),
            "n": float(tally["n"][index].sum()),
            "shares": tally["n"][index],
        }
        rows.extend(_metric_rows("sim", state, block, None, scale))
    return pd.DataFrame(rows)


def _summarize_drives(frame: pd.DataFrame, side: str) -> pd.DataFrame:
    games = max(frame["game_id"].nunique(), 1)
    rows = []
    halves = frame.groupby("half_name").size() / games
    half_counts = np.array([
        float(halves.get("half_1", 0.0)),
        float(halves.get("half_2", 0.0)),
        float(halves.get("ot", 0.0)),
    ])
    rows.extend(_metric_rows(side, "overall", _block(frame), half_counts, games))
    for state in cfg.SITUATIONS:
        rows.extend(_metric_rows(side, state, _block(frame[frame["situation"].eq(state)]), None, games))
    return pd.DataFrame(rows)


def _block(frame: pd.DataFrame) -> dict:
    shares = np.array([(frame["outcome"] == label).sum() for label in cfg.DRIVE_OUTCOMES], dtype=float)
    return {
        "plays": float(frame["scrimmage_plays"].sum()) if "scrimmage_plays" in frame else float(frame.get("plays", pd.Series(dtype=float)).sum()),
        "sec": float(frame["seconds"].sum()),
        "points": float(frame["points"].sum()),
        "yard_sum": float(frame["yard"].sum()),
        "n": float(len(frame)),
        "shares": shares,
        "scrimmage_seconds": float(frame["scrimmage_seconds"].sum()) if "scrimmage_seconds" in frame else float(frame["seconds"].sum()),
    }


def _metric_rows(side: str, state: str, block: dict, halves: np.ndarray | None, games: float) -> list[dict]:
    n = float(block["n"])
    shares = block.get("shares")
    rows = []
    if shares is not None:
        for label, count in zip(cfg.DRIVE_OUTCOMES, shares, strict=True):
            rows.append(_row(side, state, f"share_{label}", float(count) / n if n else np.nan))
    else:
        # State slices of the sim tally store shares on the row counts already folded in.
        pass
    rows.append(_row(side, state, "points_per_drive", block["points"] / n if n else np.nan))
    rows.append(_row(side, state, "plays_per_drive", block["plays"] / n if n else np.nan))
    rows.append(_row(side, state, "seconds_per_drive", block["sec"] / n if n else np.nan))
    play_seconds = block.get("scrimmage_seconds", block["sec"])
    rows.append(_row(side, state, "seconds_per_play", play_seconds / block["plays"] if block["plays"] else np.nan))
    rows.append(_row(side, state, "start_yardline", block["yard_sum"] / n if n else np.nan))
    rows.append(_row(side, state, "drives_per_game", n / games if games else np.nan))
    if halves is not None:
        rows.append(_row(side, state, "drives_per_game_half_1", float(halves[0])))
        rows.append(_row(side, state, "drives_per_game_half_2", float(halves[1])))
        rows.append(_row(side, state, "drives_per_game_ot", float(halves[2])))
    return rows


def _row(side: str, state: str, metric: str, value: float) -> dict:
    return {"side": side, "state": state, "metric": metric, "value": value}


def write_diagnostic(path: Path, actual: pd.DataFrame, simulated: pd.DataFrame) -> None:
    both = pd.concat([actual, simulated], ignore_index=True)
    wide = both.pivot_table(index=["state", "metric"], columns="side", values="value", aggfunc="first")
    wide = wide.reset_index()
    if "sim" in wide.columns and "actual" in wide.columns:
        wide["sim_minus_actual"] = wide["sim"] - wide["actual"]
    order = {"overall": 0, "neutral": 1, "leading": 2, "trailing": 3, "two_minute": 4}
    wide["_state"] = wide["state"].map(order).fillna(9)
    wide = wide.sort_values(["_state", "metric"]).drop(columns="_state")
    path.parent.mkdir(parents=True, exist_ok=True)
    wide.to_csv(path, index=False)


def sim_state_shares(tally: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Outcome counts by state, used when the sim block has no share vector."""
    return {state: tally["n"][index] for index, state in enumerate(cfg.SITUATIONS)}
