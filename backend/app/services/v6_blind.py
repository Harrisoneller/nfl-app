"""v6-blind math: opponent-adjusted team units, stage-2 OLS, grading helpers.

Projections are market-blind. Spreads, totals, moneylines and prices are not
fit targets and are not columns of the design. Sigma is the pre-registered
13.5 (margin) and 10.0 (total) from ``prediction_dist``; this module does not
retune them.

The QB term is not a second free coefficient. Pass offense is residualized by
the dropback-weighted QB already inside the team rating, and the expected
starter is added back in the same EPA/play units, so one pass coefficient
prices both.
"""
from __future__ import annotations

import math
from collections.abc import Iterable

import numpy as np
import pandas as pd

MODEL_VERSION = "qb-ridge-v6-blind"
MARGIN_SIGMA = 13.5
TOTAL_SIGMA = 10.0

# nflverse spread_line is positive when the home team is favored. The model
# margin is home score minus away score, the same direction.
MARGIN_FEATURES = ("pass_comp", "rush_comp", "sr_comp", "hfa")
TOTAL_FEATURES = ("pass_sum", "rush_sum", "sr_sum")
MARGIN_COEF_KEYS = ("pass", "rush", "success", "hfa")
TOTAL_COEF_KEYS = ("const", "pass", "rush", "success")

# Chosen on 2016–2023 walk-forward margin CRPS only. 2015 is the first
# training season. Holdout seasons are not in this grid.
HALF_LIVES = (6.0, 10.0, 14.0)
K_PASS = (6.0, 8.0)
K_RUSH = (10.0, 16.0)
K_SUCCESS = (8.0, 12.0)
DEF_WEIGHTS = (0.55, 0.75)
QB_K = (300.0, 400.0)

# Preseason: last season regressed one third toward the mean, then scaled by
# continuity. Not a searched constant.
PRESEASON_KEEP = 2.0 / 3.0
RIDGE_EPS = 1e-4

BANNED_INPUTS = (
    "spread_line", "total_line", "home_moneyline", "away_moneyline",
    "market_spread", "market_total", "market_home_win_prob", "mkt_d",
    "kalshi", "adp", "implied_prob", "closing_line", "opener",
)


def assert_blind_columns(columns: Iterable[str]) -> None:
    """Raise if a frame carries a price, a line, or a market feature."""
    bad = []
    for col in columns:
        key = str(col).lower()
        if (
            key in BANNED_INPUTS
            or key.startswith("mkt")
            or "moneyline" in key
            or any(tok in key for tok in ("spread_line", "total_line", "kalshi", "adp"))
        ):
            bad.append(str(col))
    if bad:
        raise ValueError(f"market columns are not model inputs: {bad}")


def normal_pdf(z: np.ndarray) -> np.ndarray:
    return np.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)


def normal_cdf(z: np.ndarray) -> np.ndarray:
    return 0.5 * (1.0 + np.vectorize(math.erf)(z / math.sqrt(2.0)))


def crps_normal(y: np.ndarray, mu: np.ndarray, sigma: float) -> np.ndarray:
    """Closed-form CRPS of a normal location. Sigma is fixed, so this ranks means."""
    z = (np.asarray(y, dtype=float) - np.asarray(mu, dtype=float)) / sigma
    return sigma * (z * (2.0 * normal_cdf(z) - 1.0) + 2.0 * normal_pdf(z) - 1.0 / math.sqrt(math.pi))


def wilson_interval(wins: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if n <= 0:
        return None, None
    p = wins / n
    den = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / den
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / den
    return centre - half, centre + half


def fit_margin(frame: pd.DataFrame) -> dict[str, float]:
    """OLS of home margin on the blind design. No intercept besides home field."""
    assert_blind_columns(frame.columns)
    x = frame.loc[:, MARGIN_FEATURES].to_numpy(dtype=float)
    y = frame["result"].to_numpy(dtype=float)
    coef, *_ = np.linalg.lstsq(x, y, rcond=None)
    return {k: float(np.round(v, 3)) for k, v in zip(MARGIN_COEF_KEYS, coef)}


def fit_total(frame: pd.DataFrame) -> dict[str, float]:
    assert_blind_columns(frame.columns)
    sums = frame.loc[:, TOTAL_FEATURES].to_numpy(dtype=float)
    x = np.column_stack([np.ones(len(frame)), sums])
    y = frame["total_pts"].to_numpy(dtype=float)
    coef, *_ = np.linalg.lstsq(x, y, rcond=None)
    return {k: float(np.round(v, 3)) for k, v in zip(TOTAL_COEF_KEYS, coef)}


def apply_margin(frame: pd.DataFrame, coef: dict[str, float]) -> np.ndarray:
    return (
        coef["pass"] * frame["pass_comp"].to_numpy(dtype=float)
        + coef["rush"] * frame["rush_comp"].to_numpy(dtype=float)
        + coef["success"] * frame["sr_comp"].to_numpy(dtype=float)
        + coef["hfa"] * frame["hfa"].to_numpy(dtype=float)
    )


def apply_total(frame: pd.DataFrame, coef: dict[str, float]) -> np.ndarray:
    return (
        coef["const"]
        + coef["pass"] * frame["pass_sum"].to_numpy(dtype=float)
        + coef["rush"] * frame["rush_sum"].to_numpy(dtype=float)
        + coef["success"] * frame["sr_sum"].to_numpy(dtype=float)
    )


def predict_frame(
    frame: pd.DataFrame,
    margin_coef: dict[str, float],
    total_coef: dict[str, float],
) -> pd.DataFrame:
    out = frame.copy()
    out["margin"] = apply_margin(out, margin_coef)
    out["total"] = apply_total(out, total_coef)
    return out


def component_frame(
    ratings: pd.DataFrame,
    *,
    def_weight: float,
) -> pd.DataFrame:
    """Home-minus-away team units. Defense is weighted below offense.

    ``def_*`` is EPA (or success) allowed above average, so a larger number is
    a worse defense. The starter delta is already in EPA/play and is added to
    the pass component, which is how the pass coefficient prices it once.
    """
    w = float(def_weight)
    r = ratings
    pass_edge = (r["pass_off_h"] - r["pass_off_a"]) + w * (r["pass_def_a"] - r["pass_def_h"])
    rush_edge = (r["rush_off_h"] - r["rush_off_a"]) + w * (r["rush_def_a"] - r["rush_def_h"])
    sr_edge = (r["sr_off_h"] - r["sr_off_a"]) + w * (r["sr_def_a"] - r["sr_def_h"])
    qb = r["qb_delta_h"] - r["qb_delta_a"]
    out = pd.DataFrame({
        "pass_comp": pass_edge + qb,
        "rush_comp": rush_edge,
        "sr_comp": sr_edge,
        "pass_sum": r["pass_off_h"] + r["pass_off_a"] + r["qb_delta_h"] + r["qb_delta_a"],
        "rush_sum": r["rush_off_h"] + r["rush_off_a"],
        "sr_sum": r["sr_off_h"] + r["sr_off_a"],
    }, index=r.index)
    return out


def ridge_team(
    posteam: np.ndarray,
    defteam: np.ndarray,
    is_home: np.ndarray,
    y: np.ndarray,
    weight: np.ndarray,
    n_teams: int,
    prior_off: np.ndarray,
    prior_def: np.ndarray,
    k: float,
) -> tuple[np.ndarray, np.ndarray, float, float]:
    """Weighted ridge ``y = ic + off_i + def_j + hfa·home``, shrunk toward the prior by ``k`` games."""
    n = len(y)
    if n == 0:
        return prior_off.copy(), prior_def.copy(), 0.0, 0.0
    p = 2 * n_teams + 2
    x = np.zeros((n, p), dtype=float)
    rows = np.arange(n)
    x[rows, posteam] = 1.0
    x[rows, n_teams + defteam] = 1.0
    x[:, 2 * n_teams] = 1.0
    x[:, 2 * n_teams + 1] = is_home
    pen = np.full(p, RIDGE_EPS)
    pen[:n_teams] = k
    pen[n_teams:2 * n_teams] = k
    xtw = x.T * weight
    a = xtw @ x
    a.flat[:: p + 1] += pen
    rhs = xtw @ y
    rhs[:n_teams] += k * prior_off
    rhs[n_teams:2 * n_teams] += k * prior_def
    try:
        coef = np.linalg.solve(a, rhs)
    except np.linalg.LinAlgError:
        coef = np.linalg.lstsq(a, rhs, rcond=None)[0]
    return (
        coef[:n_teams].copy(),
        coef[n_teams:2 * n_teams].copy(),
        float(coef[2 * n_teams]),
        float(coef[2 * n_teams + 1]),
    )


def select_play_gate(
    abs_edge: np.ndarray,
    covered: np.ndarray,
    *,
    ks: tuple[float, ...] = (1.0, 2.0, 3.0),
    min_n: int = 80,
    breakeven: float = 0.524,
) -> tuple[float | None, list[dict]]:
    """Smallest edge cutoff whose Wilson lower bound clears -110, or none."""
    chosen: float | None = None
    rows: list[dict] = []
    for k in ks:
        mask = abs_edge >= k
        n = int(mask.sum())
        wins = int(covered[mask].sum()) if n else 0
        lo, hi = wilson_interval(wins, n)
        ok = n >= min_n and lo is not None and lo > breakeven
        rows.append({
            "k": k,
            "n": n,
            "wins": wins,
            "ats": (wins / n) if n else None,
            "wilson_lo": lo,
            "wilson_hi": hi,
            "clears": ok,
        })
        if chosen is None and ok:
            chosen = float(k)
    return chosen, rows


def core_bar(model_crps: float, close_crps: float, model_rmse: float, close_rmse: float, ats: float) -> dict:
    crps_pass = bool(model_crps <= close_crps * 1.02)
    rmse_pass = bool(model_rmse <= close_rmse + 0.3)
    ats_pass = bool(ats >= 0.50)
    return {
        "crps_within_2pct_of_close": {
            "pass": crps_pass,
            "model": model_crps,
            "close": close_crps,
            "limit": close_crps * 1.02,
        },
        "rmse_within_close_plus_0_3": {
            "pass": rmse_pass,
            "model": model_rmse,
            "close": close_rmse,
            "limit": close_rmse + 0.3,
        },
        "ats_at_least_0_50": {
            "pass": ats_pass,
            "model": ats,
            "baseline_to_beat": 0.490,
        },
    }
