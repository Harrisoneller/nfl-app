"""Small numpy fits. No market columns and no served rating model."""

from __future__ import annotations

import numpy as np

from . import sim_config as cfg


def exp_weights(n: int, half_life: float = cfg.HALF_LIFE_GAMES) -> np.ndarray:
    """Weights for chronological values. The last value is the most recent."""
    if n <= 0:
        return np.zeros(0)
    ages = np.arange(n - 1, -1, -1, dtype=float)
    return np.exp(-np.log(2.0) * ages / half_life)


def weighted_mean(values: np.ndarray, weights: np.ndarray) -> tuple[float, float]:
    values = np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)
    ok = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    if not np.any(ok):
        return float("nan"), 0.0
    ww = weights[ok]
    neff = float((ww.sum() ** 2) / np.sum(ww ** 2))
    return float(np.average(values[ok], weights=ww)), neff


def shrink(estimate: float, n_eff: float, league: float, k: float = cfg.SHRINK_K) -> float:
    if not np.isfinite(estimate) or n_eff <= 0 or not np.isfinite(league):
        return float(league) if np.isfinite(league) else 0.0
    return float((n_eff * estimate + k * league) / (n_eff + k))


def fit_ridge(x: np.ndarray, y: np.ndarray, alpha: float) -> np.ndarray:
    """Ridge with an unpenalized intercept in column 0."""
    xtx = x.T @ x
    penalty = alpha * np.eye(x.shape[1])
    penalty[0, 0] = 0.0
    return np.linalg.solve(xtx + penalty, x.T @ y)


def predict_ridge(x: np.ndarray, coef: np.ndarray) -> np.ndarray:
    return x @ coef


def softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - logits.max(axis=1, keepdims=True)
    exp = np.exp(shifted)
    return exp / exp.sum(axis=1, keepdims=True)


def fit_softmax(x: np.ndarray, y: np.ndarray, n_classes: int, l2: float, n_iter: int) -> np.ndarray:
    """L2-penalized softmax. Returns weights shaped (n_features, n_classes).

    Full gradient steps. A one-class Newton step stalled and could not
    reproduce a training rate that lives on one binary feature.
    """
    n_rows, n_features = x.shape
    weights = np.zeros((n_features, n_classes))
    targets = np.eye(n_classes)[np.asarray(y, dtype=int)]
    step = 1.0
    for _ in range(n_iter):
        probs = softmax(x @ weights)
        grad = x.T @ (targets - probs) / n_rows
        grad[1:] -= l2 * weights[1:]
        weights += step * grad
        weights -= weights.mean(axis=1, keepdims=True)
    return weights


def crps_ensemble(samples: np.ndarray, actual: float) -> float:
    ordered = np.sort(np.asarray(samples, dtype=float))
    n = ordered.size
    if n == 0:
        return float("nan")
    index = np.arange(1, n + 1)
    pairwise = (2.0 / n**2) * np.sum((2 * index - n - 1) * ordered)
    return float(np.mean(np.abs(ordered - actual)) - 0.5 * pairwise)
