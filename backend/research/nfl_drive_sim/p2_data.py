"""Team-game and quarterback rows for the pace, yards, and drive models.

Actuals on a row are that game's results. They are labels. Priors are built
only from rows with an earlier kickoff.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import sim_config as cfg
from .teams import canon


def _num(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(0.0, index=frame.index)
    return pd.to_numeric(frame[column], errors="coerce").fillna(0.0)


def _situation(score: pd.Series, half_seconds: pd.Series) -> pd.Series:
    two = half_seconds <= cfg.TWO_MINUTE_SECONDS
    labels = pd.Series(np.full(len(score), "neutral", dtype=object), index=score.index)
    labels = labels.mask((score > cfg.NEUTRAL_SCORE) & ~two, "leading")
    labels = labels.mask((score < -cfg.NEUTRAL_SCORE) & ~two, "trailing")
    return labels.mask(two, "two_minute")


def team_game_table(plays: pd.DataFrame, drives: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    """One offense row per team-game. Weather and the dome flag come from the schedule."""
    if len(plays) == 0:
        return pd.DataFrame()
    work = plays.copy()
    work["game_id"] = work["game_id"].astype(str)
    scrimmage = work["play_type"].isin(["pass", "run", "qb_kneel", "qb_spike"])
    scrimmage = scrimmage & ~_num(work, "extra_point_attempt").eq(1) & ~_num(work, "two_point_attempt").eq(1)
    snap = work.loc[scrimmage].copy()
    if len(snap) == 0:
        return pd.DataFrame()
    snap = snap.sort_values(["game_id", "play_id"], kind="mergesort")
    snap["prev_clock"] = snap.groupby("game_id", sort=False)["game_seconds_remaining"].shift(1)
    snap["prev_team"] = snap.groupby("game_id", sort=False)["posteam"].shift(1)
    delta = _num(snap, "prev_clock") - _num(snap, "game_seconds_remaining")
    same = snap["posteam"].astype(str) == snap["prev_team"].astype(str)
    snap["snap_sec"] = delta.where(same & delta.gt(0) & delta.le(cfg.SEC_CAP), 0.0)
    snap["score"] = _num(snap, "score_differential")
    snap["half_left"] = _num(snap, "half_seconds_remaining")
    snap["situation"] = _situation(snap["score"], snap["half_left"])
    snap["is_pass"] = _num(snap, "pass").eq(1) | snap["play_type"].eq("pass")
    snap["is_rush"] = _num(snap, "rush").eq(1) | snap["play_type"].isin(["run", "qb_kneel"])
    snap["dropback"] = _num(snap, "qb_dropback").eq(1) | snap["is_pass"]
    snap["incomplete"] = snap["is_pass"] & ~_num(snap, "complete_pass").eq(1)
    snap["explosive_pass"] = snap["is_pass"] & _num(snap, "yards_gained").ge(cfg.EXPLOSIVE_PASS_YARDS)
    snap["explosive_run"] = snap["is_rush"] & _num(snap, "yards_gained").ge(cfg.EXPLOSIVE_RUN_YARDS)
    early = snap["down"].isin([1, 2]) & snap["situation"].eq("neutral")
    snap["early_pass"] = early & snap["is_pass"]
    snap["early_play"] = early

    grouped = snap.groupby(["game_id", "posteam"], sort=False)
    rows = grouped.agg(
        plays=("play_id", "size"),
        snap_sec=("snap_sec", "sum"),
        pass_plays=("is_pass", "sum"),
        rush_plays=("is_rush", "sum"),
        dropbacks=("dropback", "sum"),
        epa=("epa", "sum") if "epa" in snap.columns else ("play_id", "size"),
        success=("success", "sum") if "success" in snap.columns else ("play_id", "size"),
        incompletions=("incomplete", "sum"),
        expl_pass_n=("explosive_pass", "sum"),
        expl_run_n=("explosive_run", "sum"),
        early_pass_n=("early_pass", "sum"),
        early_play_n=("early_play", "sum"),
        sacks=("sack", "sum") if "sack" in snap.columns else ("play_id", "size"),
        qb_hits=("qb_hit", "sum") if "qb_hit" in snap.columns else ("play_id", "size"),
        no_huddle_n=("no_huddle", "sum") if "no_huddle" in snap.columns else ("play_id", "size"),
    ).reset_index()
    if "epa" not in snap.columns:
        rows["epa"] = 0.0
    if "success" not in snap.columns:
        rows["success"] = 0.0
    if "sack" not in snap.columns:
        rows["sacks"] = 0.0
    if "qb_hit" not in snap.columns:
        rows["qb_hits"] = 0.0
    if "no_huddle" not in snap.columns:
        rows["no_huddle_n"] = 0.0
    pass_yards = snap.loc[snap["is_pass"]].groupby(["game_id", "posteam"])["yards_gained"].sum()
    rush_yards = snap.loc[snap["is_rush"]].groupby(["game_id", "posteam"])["yards_gained"].sum()
    rows = rows.merge(pass_yards.rename("pass_yards"), on=["game_id", "posteam"], how="left")
    rows = rows.merge(rush_yards.rename("rush_yards"), on=["game_id", "posteam"], how="left")
    for state, key in (
        ("neutral", "neutral"),
        ("leading", "leading"),
        ("trailing", "trailing"),
        ("two_minute", "two_minute"),
    ):
        part = snap.loc[snap["situation"].eq(state)]
        n_plays = part.groupby(["game_id", "posteam"]).size()
        rows = rows.merge(n_plays.rename(f"plays_{key}"), on=["game_id", "posteam"], how="left")
    rows = rows.fillna(0.0)
    rows = rows.rename(columns={"posteam": "team"})
    rows["team"] = rows["team"].map(canon)

    drive_rows = _drive_side(drives)
    rows = rows.merge(drive_rows, on=["game_id", "team"], how="left").fillna(0.0)
    meta = games.copy()
    meta["game_id"] = meta["game_id"].astype(str)
    home = meta.rename(columns={"home_team": "team", "away_team": "opponent"})
    home["is_home"] = 1.0
    away = meta.rename(columns={"away_team": "team", "home_team": "opponent"})
    away["is_home"] = 0.0
    sides = pd.concat([home, away], ignore_index=True)
    sides["team"] = sides["team"].map(canon)
    sides["opponent"] = sides["opponent"].map(canon)
    keep = [
        "game_id", "season", "week", "game_type", "kickoff_et", "team", "opponent", "is_home",
        "roof", "temp", "wind", "home_score", "away_score", "overtime",
    ]
    for column in keep:
        if column not in sides.columns:
            sides[column] = np.nan
    sides = sides[keep]
    rows = rows.merge(sides, on=["game_id", "team"], how="inner")
    roof = rows["roof"].astype(str).str.lower()
    rows["dome"] = roof.isin(["dome", "closed"]).astype(float)
    rows["wind"] = pd.to_numeric(rows["wind"], errors="coerce").fillna(0.0)
    rows.loc[rows["dome"].eq(1), "wind"] = 0.0
    plays_n = rows["plays"].clip(lower=1)
    clock = _drive_clock(snap)
    rows["team"] = rows["team"].map(canon)
    rows["game_id"] = rows["game_id"].astype(str)
    rows = rows.merge(clock, on=["game_id", "team"], how="left")
    gaps = rows["clock_gaps"].fillna(0)
    rows["sec_per_play"] = rows["clock_span"] / gaps.clip(lower=1)
    rows.loc[gaps.lt(1), "sec_per_play"] = np.nan
    rows["plays_per_drive"] = rows["plays"] / rows["drives"].clip(lower=1)
    rows["no_huddle"] = rows["no_huddle_n"] / plays_n
    rows["incomplete"] = rows["incompletions"] / rows["pass_plays"].clip(lower=1)
    rows["pass_rate"] = rows["pass_plays"] / plays_n
    rows["epa"] = rows["epa"] / plays_n
    rows["success"] = rows["success"] / plays_n
    rows["expl_pass"] = rows["expl_pass_n"] / rows["pass_plays"].clip(lower=1)
    rows["expl_run"] = rows["expl_run_n"] / rows["rush_plays"].clip(lower=1)
    rows["early_pass"] = rows["early_pass_n"] / rows["early_play_n"].clip(lower=1)
    rows["sack"] = rows["sacks"] / rows["dropbacks"].clip(lower=1)
    rows["pressure"] = rows["qb_hits"] / rows["dropbacks"].clip(lower=1)
    rows["ypp_pass"] = rows["pass_yards"] / rows["pass_plays"].clip(lower=1)
    rows["ypp_run"] = rows["rush_yards"] / rows["rush_plays"].clip(lower=1)
    for key in ("neutral", "leading", "trailing", "two_minute"):
        gaps = rows[f"clock_gaps_{key}"].fillna(0)
        rows[f"sec_{key}"] = rows[f"clock_span_{key}"] / gaps.clip(lower=1)
        rows.loc[gaps.lt(1), f"sec_{key}"] = np.nan
        drive_plays = rows[f"drive_plays_{key}"] if f"drive_plays_{key}" in rows.columns else 0.0
        drive_n = rows[f"drive_n_{key}"] if f"drive_n_{key}" in rows.columns else 0.0
        drive_n = pd.Series(drive_n, index=rows.index).fillna(0)
        rows[f"ppd_{key}"] = drive_plays / drive_n.clip(lower=1)
        rows.loc[drive_n.lt(1), f"ppd_{key}"] = np.nan
    rows["td_rate"] = rows["td_drives"] / rows["drives"].clip(lower=1)
    rows["fg_rate"] = rows["fg_drives"] / rows["drives"].clip(lower=1)
    rows["punt_rate"] = rows["punt_drives"] / rows["drives"].clip(lower=1)
    rows["to_rate"] = rows["to_drives"] / rows["drives"].clip(lower=1)
    return rows


def _drive_clock(snap: pd.DataFrame) -> pd.DataFrame:
    """Game-clock seconds per play.

    The span is the clock at the first scrimmage play minus the clock at the
    last. Dividing by plays-minus-one estimates one play. Charging that rate
    on every play, including the first, also covers the kick or punt that
    the span does not include.
    """
    if "drive" not in snap.columns or len(snap) == 0:
        return pd.DataFrame(columns=["game_id", "team"])
    pieces = []
    for (game_id, team, _drive), part in snap.groupby(["game_id", "posteam", "drive"], sort=False):
        clocks = pd.to_numeric(part["game_seconds_remaining"], errors="coerce")
        start = clocks.iloc[0]
        end = clocks.iloc[-1]
        span = float(start - end) if pd.notna(start) and pd.notna(end) and end <= start else 0.0
        pieces.append({
            "game_id": str(game_id),
            "team": str(team),
            "span": span,
            "gaps": max(len(part) - 1, 0),
            "situation": part["situation"].iloc[0],
        })
    clock = pd.DataFrame(pieces)
    team = clock.groupby(["game_id", "team"], sort=False).agg(
        clock_span=("span", "sum"), clock_gaps=("gaps", "sum"),
    ).reset_index()
    for state, key in (
        ("neutral", "neutral"),
        ("leading", "leading"),
        ("trailing", "trailing"),
        ("two_minute", "two_minute"),
    ):
        part = clock.loc[clock["situation"].eq(state)]
        agg = part.groupby(["game_id", "team"], sort=False).agg(
            span=("span", "sum"), gaps=("gaps", "sum"),
        ).reset_index()
        agg = agg.rename(columns={"span": f"clock_span_{key}", "gaps": f"clock_gaps_{key}"})
        team = team.merge(agg, on=["game_id", "team"], how="left")
    return team


def _drive_side(drives: pd.DataFrame) -> pd.DataFrame:
    if drives is None or len(drives) == 0:
        return pd.DataFrame(columns=["game_id", "team", "drives"])
    frame = drives.copy()
    frame["game_id"] = frame["game_id"].astype(str)
    frame["team"] = frame["posteam"].map(canon)
    frame["situation"] = _situation(
        pd.to_numeric(frame["start_score_diff"], errors="coerce").fillna(0),
        pd.to_numeric(frame["start_half_seconds_remaining"], errors="coerce").fillna(1800),
    )
    totals = frame.groupby(["game_id", "team"], sort=False).agg(
        drives=("drive", "size"),
        drive_plays=("plays", "sum"),
        td_drives=("outcome", lambda s: int(s.eq("TD").sum())),
        fg_drives=("outcome", lambda s: int(s.eq("FG_MADE").sum())),
        punt_drives=("outcome", lambda s: int(s.eq("PUNT").sum())),
        to_drives=("outcome", lambda s: int(s.eq("TURNOVER").sum())),
    ).reset_index()
    for state, key in (
        ("neutral", "neutral"),
        ("leading", "leading"),
        ("trailing", "trailing"),
        ("two_minute", "two_minute"),
    ):
        part = frame.loc[frame["situation"].eq(state)]
        counts = part.groupby(["game_id", "team"]).agg(
            n=("drive", "size"),
            play_sum=("plays", "sum"),
        ).reset_index()
        counts = counts.rename(columns={"n": f"drive_n_{key}", "play_sum": f"drive_plays_{key}"})
        totals = totals.merge(counts, on=["game_id", "team"], how="left")
    return totals.fillna(0.0)


def qb_game_table(plays: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    """Dropback EPA and CPOE by the passer who played. Used only as that player's history."""
    if len(plays) == 0 or "passer_player_id" not in plays.columns:
        return pd.DataFrame(columns=["game_id", "kickoff_et", "qb_id", "epa", "cpoe", "dropbacks"])
    snap = plays.copy()
    drop = _num(snap, "qb_dropback").eq(1) | _num(snap, "pass").eq(1) | snap["play_type"].eq("pass")
    snap = snap.loc[drop & snap["passer_player_id"].notna()]
    if len(snap) == 0:
        return pd.DataFrame(columns=["game_id", "kickoff_et", "qb_id", "epa", "cpoe", "dropbacks"])
    grouped = snap.groupby(["game_id", "passer_player_id"], sort=False).agg(
        epa=("epa", "mean") if "epa" in snap.columns else ("play_id", "size"),
        cpoe=("cpoe", "mean") if "cpoe" in snap.columns else ("play_id", "size"),
        dropbacks=("play_id", "size"),
    ).reset_index()
    if "epa" not in snap.columns:
        grouped["epa"] = 0.0
    if "cpoe" not in snap.columns:
        grouped["cpoe"] = 0.0
    grouped = grouped.rename(columns={"passer_player_id": "qb_id"})
    kick = games[["game_id", "kickoff_et"]].copy()
    kick["game_id"] = kick["game_id"].astype(str)
    grouped["game_id"] = grouped["game_id"].astype(str)
    return grouped.merge(kick, on="game_id", how="left")
