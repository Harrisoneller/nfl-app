"""Offline walk-forward refit of the spread residual.

The live spread model was trained against a negated nflverse close. This
module fits that old target and the corrected one on the same features and
scores both against the close. It does not write to a database.

The close is nflverse ``spread_line``: the home margin, positive when the
home team is favored. That is the number stored as ``closing_spread``.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from . import ml_predictions_service, prediction_dist

_FEATURES = ml_predictions_service._FEATURES
_SIGMA = prediction_dist.NFL_MARGIN_SIGMA


def baseline_margin(spread_line: pd.Series, *, negate: bool) -> pd.Series:
    """Home-margin baseline. ``negate`` is the old live model."""
    line = spread_line.astype(float)
    return -line if negate else line


def fit_spread_residual(
    train: pd.DataFrame,
    test: pd.DataFrame,
    *,
    negate_baseline: bool,
) -> np.ndarray:
    """Predict test home margins. Train only on rows in ``train``."""
    xgb = ml_predictions_service._xgb()
    if xgb is None:
        raise RuntimeError("xgboost is not installed")
    model = xgb.XGBRegressor(
        n_estimators=260,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.85,
        colsample_bytree=0.85,
        random_state=42,
        n_jobs=2,
    )
    train_base = baseline_margin(train["market_spread_home"], negate=negate_baseline)
    y = train["home_margin"].astype(float) - train_base.fillna(0.0)
    weights = ml_predictions_service._sample_weight(train)
    model.fit(train[_FEATURES].astype(float).fillna(0.0), y, sample_weight=weights)
    resid = model.predict(test[_FEATURES].astype(float).fillna(0.0))
    test_base = baseline_margin(test["market_spread_home"], negate=negate_baseline)
    return test_base.fillna(0.0).to_numpy(dtype=float) + np.asarray(resid, dtype=float)


def ats_hit(pred_margin: float, close: float, actual_margin: float) -> float:
    """1 if the side the model takes covers the close, else 0. NaN if no bet or a push."""
    if pred_margin == close or actual_margin == close:
        return float("nan")
    model_picks_home = pred_margin > close
    home_covered = actual_margin > close
    return 1.0 if model_picks_home == home_covered else 0.0


def score_frame(frame: pd.DataFrame, pred_margin: np.ndarray) -> dict[str, np.ndarray]:
    actual = frame["home_margin"].to_numpy(dtype=float)
    close = frame["market_spread_home"].to_numpy(dtype=float)
    pred = np.asarray(pred_margin, dtype=float)
    mae = np.abs(pred - actual)
    crps = np.array([
        prediction_dist.crps_normal(float(p), _SIGMA, float(a))
        for p, a in zip(pred, actual)
    ])
    ats = np.array([
        ats_hit(float(p), float(c), float(a))
        for p, c, a in zip(pred, close, actual)
    ])
    return {"mae": mae, "crps": crps, "ats": ats}


def bootstrap_mean_ci(
    values: np.ndarray,
    *,
    n_boot: int = 2000,
    seed: int = 42,
) -> dict[str, float | int | None]:
    """Percentile interval for the mean. NaN values are dropped."""
    clean = np.asarray(values, dtype=float)
    clean = clean[np.isfinite(clean)]
    n = int(clean.size)
    if n == 0:
        return {"estimate": None, "lo": None, "hi": None, "n": 0}
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, n, size=(n_boot, n))
    means = clean[draws].mean(axis=1)
    lo, hi = np.quantile(means, [0.025, 0.975])
    return {
        "estimate": float(clean.mean()),
        "lo": float(lo),
        "hi": float(hi),
        "n": n,
    }


def walk_forward(
    games: pd.DataFrame,
    *,
    test_seasons: range,
) -> pd.DataFrame:
    """Fit the old and corrected residuals on prior seasons only.

    ``games`` must already be regular-season rows with a closing line.
    """
    pieces: list[pd.DataFrame] = []
    for season in test_seasons:
        train = games[games["season"] < season]
        test = games[games["season"] == season]
        if len(train) < 200 or len(test) == 0:
            continue
        old = fit_spread_residual(train, test, negate_baseline=True)
        new = fit_spread_residual(train, test, negate_baseline=False)
        scored_old = score_frame(test, old)
        scored_new = score_frame(test, new)
        piece = test[["season", "game_id", "home_margin", "market_spread_home"]].copy()
        for name, scored in (("live", scored_old), ("refit", scored_new)):
            for metric, values in scored.items():
                piece[f"{name}_{metric}"] = values
        pieces.append(piece)
    if not pieces:
        return pd.DataFrame()
    return pd.concat(pieces, ignore_index=True)


def summarize(scored: pd.DataFrame, *, n_boot: int = 2000, seed: int = 42) -> dict[str, Any]:
    """Overall means and paired bootstrap intervals."""
    out: dict[str, Any] = {"n_games": int(len(scored)), "seasons": sorted(scored["season"].unique().tolist())}
    for metric in ("mae", "crps", "ats"):
        live = scored[f"live_{metric}"].to_numpy(dtype=float)
        refit = scored[f"refit_{metric}"].to_numpy(dtype=float)
        out[f"live_{metric}"] = bootstrap_mean_ci(live, n_boot=n_boot, seed=seed)
        out[f"refit_{metric}"] = bootstrap_mean_ci(refit, n_boot=n_boot, seed=seed + 1)
        # Pair on games where both sides are finite (ATS drops pushes).
        mask = np.isfinite(live) & np.isfinite(refit)
        delta = refit[mask] - live[mask]
        out[f"delta_{metric}"] = bootstrap_mean_ci(delta, n_boot=n_boot, seed=seed + 2)
    return out
