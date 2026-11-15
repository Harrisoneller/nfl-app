"""Holm step-down and the season-week cluster bootstrap.

The confirmatory family is the three preregistered features. The test is
one-sided: the mean of ``delta = CRPS(candidate) - CRPS(base)`` is negative
when the candidate scores better. Familywise alpha is 0.025.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .specs import (
    BOOTSTRAP_DRAWS,
    BOOTSTRAP_SEED,
    BOOTSTRAP_UPPER_Q,
    FEATURES,
    HOLM_ALPHA,
)


class HolmError(ValueError):
    """The confirmatory family is not the three locked features."""


def holm_stepdown(
    p_values: dict[str, float],
    *,
    alpha: float = HOLM_ALPHA,
) -> list[dict[str, object]]:
    """Holm step-down. Later hypotheses are not rejected after the first miss.

    Ties break on the feature name so the order is stable.
    """
    if len(p_values) == 0:
        raise HolmError("no p-values")
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


def assert_confirmatory_family(names: set[str] | list[str]) -> None:
    expected = set(FEATURES)
    got = set(names)
    if got != expected:
        raise HolmError(f"confirmatory family must be {sorted(expected)}, got {sorted(got)}")


def resampled_means(
    cluster_sum: np.ndarray,
    cluster_n: np.ndarray,
    draws: np.ndarray,
) -> np.ndarray:
    """Mean of ``delta`` when ``draws`` resamples whole clusters."""
    sums = cluster_sum[draws].sum(axis=1)
    counts = cluster_n[draws].sum(axis=1)
    return sums / counts


def cluster_bootstrap_means(
    delta,
    season,
    week,
    *,
    n_draws: int = BOOTSTRAP_DRAWS,
    seed: int = BOOTSTRAP_SEED,
) -> np.ndarray:
    """Resample ``(season, week)`` clusters with replacement.

    A cluster drawn twice contributes its games twice. Games inside a cluster
    stay together.
    """
    frame = pd.DataFrame({
        "delta": np.asarray(delta, dtype=float),
        "season": np.asarray(season, dtype=int),
        "week": np.asarray(week, dtype=int),
    })
    if not np.isfinite(frame["delta"]).all():
        raise HolmError("bootstrap delta must be finite")
    grouped = frame.groupby(["season", "week"], sort=True)["delta"]
    cluster_sum = grouped.sum().to_numpy(dtype=float)
    cluster_n = grouped.size().to_numpy(dtype=float)
    n_clusters = len(cluster_sum)
    if n_clusters < 2:
        raise HolmError("cluster bootstrap needs at least two season-weeks")
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, n_clusters, size=(n_draws, n_clusters))
    return resampled_means(cluster_sum, cluster_n, draws)


def one_sided_p_negative(boot_means: np.ndarray) -> float:
    """Conservative one-sided p for H1: mean(delta) < 0.

    ``(1 + #{means >= 0}) / (B + 1)``.
    """
    means = np.asarray(boot_means, dtype=float)
    return float((1 + np.sum(means >= 0.0)) / (len(means) + 1))


def upper_bound(boot_means: np.ndarray, quantile: float = BOOTSTRAP_UPPER_Q) -> float:
    """97.5th percentile of the bootstrap means. The gate is strict ``< 0``."""
    return float(np.quantile(np.asarray(boot_means, dtype=float), quantile, method="linear"))


def sign_matches(coef: float, expected_sign: int) -> bool:
    """True only when the coefficient is strictly on the preregistered side."""
    if coef == 0.0 or not np.isfinite(coef):
        return False
    return int(np.sign(coef)) == int(expected_sign)


def decide_feature(
    *,
    feature: str,
    p_value: float,
    holm_reject: bool,
    bound: float,
    coef: float,
) -> dict[str, object]:
    """One feature passes only when Holm rejects, the upper bound is < 0, and the sign matches."""
    spec = FEATURES[feature]
    sign_ok = sign_matches(coef, int(spec["expected_sign"]))
    bound_ok = bool(np.isfinite(bound) and bound < 0.0)
    passed = bool(holm_reject and bound_ok and sign_ok)
    return {
        "feature": feature,
        "endpoint": spec["endpoint"],
        "p": p_value,
        "holm_reject": holm_reject,
        "upper_bound": bound,
        "upper_bound_negative": bound_ok,
        "coefficient": coef,
        "expected_sign": int(spec["expected_sign"]),
        "sign_matches": sign_ok,
        "pass": passed,
    }
