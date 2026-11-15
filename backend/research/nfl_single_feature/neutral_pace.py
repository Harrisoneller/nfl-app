"""Combined neutral seconds per play.

``neutral_pace_sum`` is the home offense's prior neutral seconds per play plus
the away offense's. Smaller is faster. The expected stage-2 sign on the total
is negative. Weather, roof, and dome are not read.

v5 already carries plays per game on the total. This column is the incremental
neutral-pace term. It does not replace ``plays_s``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .design import DesignError
from .specs import (
    PACE_CLOCK_CAP,
    PACE_LAST_TWO_MINUTES,
    PACE_MIN_DELTAS,
    PACE_SCORE_WITHIN,
    PACE_SEASONS_BACK,
    PACE_WP_HI,
    PACE_WP_LO,
    REGIME_LAST,
)


def _late_first_half(plays: pd.DataFrame) -> pd.Series:
    """True on second-quarter plays inside the last two minutes, or with no clock.

    The last two minutes of the second half fall in the fourth quarter, which
    is already outside Q1–Q3. End-of-quarter plays in Q1 and Q3 stay in.
    """
    if "half_seconds_remaining" in plays.columns:
        clock = plays["half_seconds_remaining"]
    elif "quarter_seconds_remaining" in plays.columns:
        clock = plays["quarter_seconds_remaining"]
    else:
        raise DesignError("neutral pace needs half_seconds_remaining or quarter_seconds_remaining")
    qtr2 = plays["qtr"].astype(float).eq(2.0)
    return qtr2 & ~(clock.astype(float) > PACE_LAST_TWO_MINUTES)


def neutral_clock_deltas(plays: pd.DataFrame) -> pd.DataFrame:
    """Positive clock deltas, in ``(0, 40]`` seconds, on neutral scrimmage snaps."""
    required = {
        "game_id", "season", "week", "qtr", "posteam", "wp", "score_differential",
        "pass", "rush", "qb_kneel", "qb_spike", "game_seconds_remaining", "drive",
    }
    missing = required - set(plays.columns)
    if missing:
        raise DesignError(f"pace frame missing columns: {sorted(missing)}")
    frame = plays[plays["season"].astype(int) > REGIME_LAST].copy()
    mask = frame["qtr"].isin([1, 2, 3])
    mask &= frame["score_differential"].abs() <= PACE_SCORE_WITHIN
    mask &= frame["wp"].between(PACE_WP_LO, PACE_WP_HI, inclusive="both")
    mask &= ~_late_first_half(frame)
    mask &= frame["pass"].fillna(0).astype(float).eq(1.0) | frame["rush"].fillna(0).astype(float).eq(1.0)
    mask &= frame["qb_kneel"].fillna(0).astype(float).ne(1.0)
    mask &= frame["qb_spike"].fillna(0).astype(float).ne(1.0)
    if "special_teams_play" in frame.columns:
        mask &= frame["special_teams_play"].fillna(0).astype(float).ne(1.0)
    kept = frame.loc[mask].copy()
    kept = kept.dropna(subset=["game_seconds_remaining", "drive", "posteam", "game_id"])
    kept = kept.sort_values(
        ["game_id", "posteam", "drive", "game_seconds_remaining"],
        ascending=[True, True, True, False],
    )
    kept["delta"] = kept.groupby(["game_id", "posteam", "drive"], sort=False)["game_seconds_remaining"].diff(-1)
    deltas = kept[(kept["delta"] > 0) & (kept["delta"] <= PACE_CLOCK_CAP)]
    return deltas[["game_id", "season", "week", "posteam", "delta"]].reset_index(drop=True)


def pace_asof(deltas: pd.DataFrame, team: str, season: int, week: int) -> float:
    """Mean prior neutral seconds per play. Missing when the snap count is under 30."""
    hist = deltas[
        (deltas["posteam"] == team)
        & (deltas["season"].astype(int) >= season - PACE_SEASONS_BACK)
        & (deltas["season"].astype(int) > REGIME_LAST)
        & (
            (deltas["season"].astype(int) < season)
            | ((deltas["season"].astype(int) == season) & (deltas["week"].astype(int) < week))
        )
    ]
    if len(hist) < PACE_MIN_DELTAS:
        return float("nan")
    return float(hist["delta"].mean())


def neutral_pace_feature(plays: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    """Sum of the two offenses' prior neutral seconds per play.

    ``games`` columns: ``game_id, season, week, home, away``.
    """
    required = {"game_id", "season", "week", "home", "away"}
    missing = required - set(games.columns)
    if missing:
        raise DesignError(f"pace matchup frame missing columns: {sorted(missing)}")
    deltas = neutral_clock_deltas(plays)
    rows: list[dict[str, object]] = []
    for game in games.itertuples(index=False):
        home = pace_asof(deltas, game.home, int(game.season), int(game.week))
        away = pace_asof(deltas, game.away, int(game.season), int(game.week))
        total = home + away if np.isfinite(home) and np.isfinite(away) else np.nan
        rows.append({
            "game_id": game.game_id,
            "season": int(game.season),
            "week": int(game.week),
            "neutral_pace_sum": total,
        })
    return pd.DataFrame(rows)
