"""Opponent-adjusted EPA core — the fundamentals layer under game predictions.

Why this exists
---------------
Raw per-play means (EPA/play, success rate) are contaminated by schedule: a
+0.10 EPA offense that has faced five bottom-10 defenses is not a +0.10
offense. This module solves the standard fix — a ridge regression on
game-level observations:

    epa_ij  =  intercept  +  off_i  +  def_j  +  hfa · home_ij  +  ε

where ``off_i`` / ``def_j`` are one-hot team effects. Every team's offensive
effect is estimated *simultaneously* with every defense it faced, so strength
of schedule falls out of the coefficients directly (same family as DVOA's
iterative opponent pass and rbsdm-style adjusted EPA).

Ridge (L2) shrinkage doubles as the early-season stabilizer: with few games,
coefficients shrink toward 0 (league average) instead of chasing noise. The
lambda is expressed in *pseudo-games* — an effect needs roughly that many
games of evidence before it's trusted at full strength. A prior-season blend
(also in pseudo-games) covers weeks 1–4 before in-season data dominates.

Outputs are **deviations from league average** in the metric's own units
(EPA/play, success-rate points). Offense: positive = good. Defense: positive
= allows more than average = bad. ``predictions_service`` converts these to
points; this module never touches points.

Also computed here (not opponent-adjusted — they're already stable/contextual):
CPOE (QB accuracy over expectation), PROE (pass-rate over expected, neutral
situations), and neutral-situation pace (seconds per snap).

Pure numpy — the design matrix is ~(games·2) × 65, closed-form solve.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..logging_config import get_logger

log = get_logger(__name__)

# Import-safe fallbacks — live values resolve through param_registry at call
# time (never at import; see param_registry module docstring).
RIDGE_LAMBDA_GAMES = 6.0     # pseudo-games of shrinkage toward league average
PRIOR_SEASON_GAMES = 3.0     # pseudo-games of last season's adjusted values

# Neutral situation: competitive game state, first 3 quarters.
_NEUTRAL_WP = (0.20, 0.80)

ADJUSTED_KEYS = (
    "adj_off_epa_per_play", "adj_def_epa_per_play",
    "adj_off_success_rate", "adj_def_success_rate",
)


def _ridge_lambda() -> float:
    try:
        from . import param_registry
        return param_registry.value("epa.ridge_lambda")
    except Exception:  # noqa: BLE001 — pipeline must run in scripts/tests without DB
        return RIDGE_LAMBDA_GAMES


def _prior_games() -> float:
    try:
        from . import param_registry
        return param_registry.value("epa.prior_weight_games")
    except Exception:  # noqa: BLE001
        return PRIOR_SEASON_GAMES


# ---- Game-level observation table ------------------------------------------


def game_level_rows(pbp: pd.DataFrame) -> pd.DataFrame:
    """One row per (game_id, posteam): mean EPA/play, success rate, plays, home flag.

    Expects pbp already filtered to pass/run plays with canonical team codes.
    """
    need = {"game_id", "posteam", "defteam", "epa", "success", "home_team"}
    if not need.issubset(pbp.columns):
        return pd.DataFrame()
    g = (
        pbp.dropna(subset=["posteam", "defteam", "epa"])
        .groupby(["game_id", "posteam", "defteam"], as_index=False)
        .agg(
            epa=("epa", "mean"),
            success=("success", "mean"),
            plays=("epa", "size"),
            home_team=("home_team", "first"),
        )
    )
    g["is_home"] = (g["posteam"] == g["home_team"]).astype(float)
    return g


def ridge_adjust(
    rows: pd.DataFrame, metric: str, lam_games: float,
) -> tuple[dict[str, float], dict[str, float]]:
    """Fit off/def team effects for one metric. Returns (off_dev, def_dev).

    Weighted ridge: each game-row weighted by play count, penalty scaled so
    ``lam_games`` reads as pseudo-games of average length. Effects are
    recentered to mean 0 (deviation from league average).
    """
    if rows.empty:
        return {}, {}
    teams = sorted(set(rows["posteam"]) | set(rows["defteam"]))
    t_idx = {t: i for i, t in enumerate(teams)}
    n_t = len(teams)
    n = len(rows)

    # Columns: [off effects | def effects | intercept | home]
    x = np.zeros((n, 2 * n_t + 2))
    y = rows[metric].to_numpy(dtype=float)
    w = rows["plays"].to_numpy(dtype=float)
    x[np.arange(n), [t_idx[t] for t in rows["posteam"]]] = 1.0
    x[np.arange(n), [n_t + t_idx[t] for t in rows["defteam"]]] = 1.0
    x[:, 2 * n_t] = 1.0
    x[:, 2 * n_t + 1] = rows["is_home"].to_numpy(dtype=float)

    avg_plays = float(w.mean()) if n else 1.0
    lam = lam_games * avg_plays  # pseudo-games → play-weight units
    penalty = np.full(2 * n_t + 2, lam)
    penalty[2 * n_t:] = 1e-6  # don't shrink intercept / HFA

    xtw = x.T * w
    a = xtw @ x + np.diag(penalty)
    b = xtw @ y
    try:
        coef = np.linalg.solve(a, b)
    except np.linalg.LinAlgError:  # pragma: no cover — penalty makes A PD
        coef = np.linalg.lstsq(a, b, rcond=None)[0]

    off = {t: float(coef[t_idx[t]]) for t in teams}
    deff = {t: float(coef[n_t + t_idx[t]]) for t in teams}
    off_mean = float(np.mean(list(off.values())))
    def_mean = float(np.mean(list(deff.values())))
    return (
        {t: v - off_mean for t, v in off.items()},
        {t: v - def_mean for t, v in deff.items()},
    )


# ---- Context metrics: CPOE / PROE / neutral pace ---------------------------


def context_metrics(pbp: pd.DataFrame) -> dict[str, dict[str, float]]:
    """Per-team CPOE, PROE (neutral), and neutral-situation seconds per snap."""
    out: dict[str, dict[str, float]] = {}
    lo, hi = _NEUTRAL_WP
    has_wp = "wp" in pbp.columns
    neutral = (
        pbp[(pbp["wp"].between(lo, hi)) & (pbp["qtr"] <= 3)]
        if has_wp and "qtr" in pbp.columns else pbp.iloc[0:0]
    )

    for tm, plays in pbp.groupby("posteam"):
        if not tm:
            continue
        row = out.setdefault(tm, {})
        if "cpoe" in plays.columns:
            cpoe = plays["cpoe"].dropna()
            if len(cpoe):
                row["off_cpoe"] = float(cpoe.mean())

    for tm, plays in neutral.groupby("posteam"):
        if not tm:
            continue
        row = out.setdefault(tm, {})
        if "pass_oe" in plays.columns:
            proe = plays["pass_oe"].dropna()
            if len(proe):
                row["off_proe"] = float(proe.mean())  # pct points over expected
        # Pace: elapsed clock between consecutive snaps within the same drive.
        if {"game_seconds_remaining", "game_id", "drive"}.issubset(plays.columns):
            p = plays.sort_values("game_seconds_remaining", ascending=False)
            deltas = p.groupby(["game_id", "drive"])["game_seconds_remaining"].diff(-1)
            deltas = deltas.dropna()
            deltas = deltas[(deltas > 0) & (deltas <= 60)]  # drop breaks/timeouts
            if len(deltas) >= 20:
                row["off_neutral_sec_per_play"] = float(deltas.mean())
    return out


# ---- Top-level entry --------------------------------------------------------


def compute_adjusted_metrics(
    pbp: pd.DataFrame,
    prior: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, float]]:
    """Full adjusted-EPA pass for one season of (pass/run-filtered) PBP.

    ``prior`` is last season's aggregates dict; its adjusted values are blended
    in at ``epa.prior_weight_games`` pseudo-games so week-2 numbers aren't pure
    small-sample ridge output. Returns {team: {metric: value}} ready to merge
    into the season aggregates.
    """
    rows = game_level_rows(pbp)
    if rows.empty:
        return {}
    lam = _ridge_lambda()
    off_epa, def_epa = ridge_adjust(rows, "epa", lam)
    off_sr, def_sr = ridge_adjust(rows, "success", lam)

    games_played = rows.groupby("posteam")["game_id"].nunique().to_dict()

    out: dict[str, dict[str, float]] = {}
    for tm in off_epa:
        out[tm] = {
            "adj_off_epa_per_play": off_epa[tm],
            "adj_def_epa_per_play": def_epa.get(tm, 0.0),
            "adj_off_success_rate": off_sr.get(tm, 0.0),
            "adj_def_success_rate": def_sr.get(tm, 0.0),
            "adj_games_played": float(games_played.get(tm, 0)),
        }

    # Prior-season blend: pseudo-game weighting, prior regressed 50% toward
    # league average (year-over-year team correlation is well under 1.0).
    pw = _prior_games()
    if prior and pw > 0:
        for tm, row in out.items():
            p = prior.get(tm) or {}
            gp = row["adj_games_played"]
            w_prior = pw / (pw + gp) if (pw + gp) > 0 else 0.0
            for k in ADJUSTED_KEYS:
                pv = p.get(k)
                if pv is not None:
                    row[k] = (1 - w_prior) * row[k] + w_prior * (0.5 * float(pv))

    # Context metrics ride along un-adjusted.
    for tm, ctx in context_metrics(pbp).items():
        out.setdefault(tm, {}).update(ctx)
    return out
