"""Holm step-down and the wild cluster bootstrap.

Clusters are ``(season, week)``. Draws use Webb's six-point weights, 10,000
times, seed 20261115. A cluster-robust t with ``df = C - 1`` is available as
the documented alternative. The gates use the wild bootstrap.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .specs import BOOTSTRAP_DRAWS, BOOTSTRAP_SEED, FEATURES, HOLM_ALPHA

# Each point has probability 1/6. Mean 0, variance 1.
WEBB_WEIGHTS = np.array([
    -math.sqrt(1.5),
    -1.0,
    -math.sqrt(0.5),
    math.sqrt(0.5),
    1.0,
    math.sqrt(1.5),
])


class HolmError(ValueError):
    """The confirmatory family is outside the three preregistered features."""


def holm_stepdown(
    p_values: dict[str, float],
    *,
    alpha: float = HOLM_ALPHA,
) -> list[dict[str, object]]:
    """Holm step-down. Later hypotheses are not rejected after the first miss.

    Ties break on the feature name. The family size is the number of features
    that reached the confirmatory stage, not always three.
    """
    if len(p_values) == 0:
        raise HolmError("no p-values")
    unknown = set(p_values) - set(FEATURES)
    if unknown:
        raise HolmError(f"feature outside the preregistered three: {sorted(unknown)}")
    ordered = sorted(p_values.items(), key=lambda item: (float(item[1]), item[0]))
    family = len(ordered)
    rows: list[dict[str, object]] = []
    still = True
    for rank, (name, p_value) in enumerate(ordered, start=1):
        threshold = alpha / (family - rank + 1)
        reject = bool(still and float(p_value) <= threshold)
        if not reject:
            still = False
        rows.append({
            "feature": name,
            "p": float(p_value),
            "rank": rank,
            "threshold": threshold,
            "reject": reject,
        })
    return rows


def _cluster_totals(values, season, week) -> tuple[np.ndarray, np.ndarray]:
    frame = pd.DataFrame({
        "value": np.asarray(values, dtype=float),
        "season": np.asarray(season, dtype=int),
        "week": np.asarray(week, dtype=int),
    })
    if not np.isfinite(frame["value"]).all():
        raise HolmError("bootstrap values must be finite")
    grouped = frame.groupby(["season", "week"], sort=True)["value"]
    sums = grouped.sum().to_numpy(dtype=float)
    counts = grouped.size().to_numpy(dtype=float)
    if len(sums) < 2:
        raise HolmError("wild cluster bootstrap needs at least two season-weeks")
    return sums, counts


def wild_cluster_replicates(
    values,
    season,
    week,
    *,
    n_draws: int = BOOTSTRAP_DRAWS,
    seed: int = BOOTSTRAP_SEED,
    null_mean: float | None = None,
) -> np.ndarray:
    """Wild cluster replicates of the sample mean.

    ``null_mean is None`` leaves the replicates centered at the sample mean
    (the unrestricted draw used for bounds). A numeric null, including 0,
    recenters the cluster scores there (the restricted draw used for a p-value).
    """
    sums, counts = _cluster_totals(values, season, week)
    total_n = float(counts.sum())
    observed = float(sums.sum() / total_n)
    center = observed if null_mean is None else float(null_mean)
    residual = sums - counts * center
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, len(WEBB_WEIGHTS), size=(n_draws, len(sums)))
    weights = WEBB_WEIGHTS[pick]
    shock = (weights * residual).sum(axis=1) / total_n
    return center + shock


def bootstrap_quantile(replicates: np.ndarray, quantile: float) -> float:
    """Linear quantile of wild-cluster replicate means."""
    return float(np.quantile(np.asarray(replicates, dtype=float), quantile, method="linear"))


def one_sided_p_negative(observed: float, null_replicates: np.ndarray) -> float:
    """One-sided p for H1: mean < 0, from replicates drawn under mean = 0.

    ``(1 + #{replicates <= observed}) / (B + 1)``.
    """
    draws = np.asarray(null_replicates, dtype=float)
    return float((1 + np.sum(draws <= observed)) / (len(draws) + 1))


def cluster_robust_t(values, season, week) -> dict[str, float]:
    """Cluster-robust t for the mean. ``df = C - 1``. Not a gate."""
    sums, counts = _cluster_totals(values, season, week)
    total_n = float(counts.sum())
    clusters = len(sums)
    mean = float(sums.sum() / total_n)
    score = sums - counts * mean
    se = math.sqrt(clusters / (clusters - 1) * float(np.sum(score ** 2))) / total_n
    if se == 0.0:
        t_stat = 0.0 if mean == 0.0 else math.copysign(math.inf, mean)
    else:
        t_stat = mean / se
    return {"mean": mean, "se": se, "t": t_stat, "df": float(clusters - 1)}


def sign_matches(coef: float, expected_sign: int) -> bool:
    """True only when the coefficient is strictly on the preregistered side."""
    if coef == 0.0 or not np.isfinite(coef):
        return False
    return int(np.sign(coef)) == int(expected_sign)
