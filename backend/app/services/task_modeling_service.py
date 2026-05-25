"""Task-oriented modeling helpers for residual + calibration workflows."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


TASK_WIN_PROBABILITY = "win_probability"
TASK_SPREAD_EDGE = "spread_edge"
TASK_PLAYER_PROPS = "player_props"
TASK_FANTASY_PROJECTION = "fantasy_projection"


@dataclass(frozen=True)
class TaskModelConfig:
    task: str
    target_kind: str
    model_version: str
    feature_set_version: str
    enabled: bool = True
    is_scaffold: bool = False


TASK_CONFIGS: dict[str, TaskModelConfig] = {
    TASK_WIN_PROBABILITY: TaskModelConfig(
        task=TASK_WIN_PROBABILITY,
        target_kind="residual_probability",
        model_version="ml-win-residual-v1",
        feature_set_version="ml-win-features-v1",
    ),
    TASK_SPREAD_EDGE: TaskModelConfig(
        task=TASK_SPREAD_EDGE,
        target_kind="residual_margin",
        model_version="ml-spread-residual-v1",
        feature_set_version="ml-spread-features-v1",
    ),
    TASK_PLAYER_PROPS: TaskModelConfig(
        task=TASK_PLAYER_PROPS,
        target_kind="scaffold",
        model_version="ml-player-props-v1",
        feature_set_version="ml-player-props-features-v1",
        enabled=False,
        is_scaffold=True,
    ),
    TASK_FANTASY_PROJECTION: TaskModelConfig(
        task=TASK_FANTASY_PROJECTION,
        target_kind="scaffold",
        model_version="ml-fantasy-projection-v1",
        feature_set_version="ml-fantasy-features-v1",
        enabled=False,
        is_scaffold=True,
    ),
}


def exponential_recency_weight(
    *,
    current_index: int,
    sample_index: int,
    half_life: float = 6.0,
) -> float:
    """Return exp-decay style recency weight in (0, 1]."""
    if half_life <= 0:
        return 1.0
    age = max(0, current_index - sample_index)
    return float(math.exp(-math.log(2.0) * (age / half_life)))


def weather_class(
    *,
    roof: str | None = None,
    wind_mph: float | None = None,
    precip_in: float | None = None,
    temperature_f: float | None = None,
) -> str:
    roof_norm = (roof or "").strip().lower()
    if roof_norm in {"closed", "dome", "indoor"}:
        return "indoor"
    if wind_mph is not None and wind_mph >= 18:
        return "windy"
    if precip_in is not None and precip_in >= 0.2:
        return "wet"
    if temperature_f is not None and temperature_f <= 32:
        return "cold"
    return "mild"


def market_baseline(
    *,
    market_spread_home: float | None,
    market_home_win_prob: float | None,
) -> dict[str, Any]:
    baseline_margin = None if market_spread_home is None else -float(market_spread_home)
    baseline_prob = None if market_home_win_prob is None else float(market_home_win_prob)
    return {
        "available": baseline_margin is not None or baseline_prob is not None,
        "baseline_home_margin": baseline_margin,
        "baseline_home_win_prob": baseline_prob,
    }


def combine_market_and_residual(
    *,
    baseline_value: float | None,
    residual_value: float,
    fallback_baseline: float,
) -> float:
    base = fallback_baseline if baseline_value is None else float(baseline_value)
    return float(base + residual_value)


def apply_platt_calibration(probability: float, artifact: dict[str, Any] | None) -> float:
    """Apply Platt scaling coefficients to a probability."""
    p = min(max(float(probability), 1e-6), 1 - 1e-6)
    if not artifact or artifact.get("method") != "platt":
        return p
    slope = float(artifact.get("slope", 1.0))
    intercept = float(artifact.get("intercept", 0.0))
    logit = math.log(p / (1 - p))
    calibrated = 1.0 / (1.0 + math.exp(-(slope * logit + intercept)))
    return min(max(calibrated, 1e-6), 1 - 1e-6)


def fit_platt_calibration(
    *,
    raw_probs: list[float],
    outcomes: list[int],
    epochs: int = 300,
    learning_rate: float = 0.08,
) -> dict[str, Any]:
    """Fit a tiny logistic layer over model probabilities."""
    if len(raw_probs) < 20 or len(raw_probs) != len(outcomes):
        return {"method": "identity", "slope": 1.0, "intercept": 0.0, "n": len(raw_probs)}

    logits = []
    ys = []
    for p, y in zip(raw_probs, outcomes):
        pp = min(max(float(p), 1e-6), 1 - 1e-6)
        logits.append(math.log(pp / (1 - pp)))
        ys.append(1.0 if int(y) == 1 else 0.0)

    slope = 1.0
    intercept = 0.0
    n = float(len(logits))
    for _ in range(max(1, epochs)):
        g_s = 0.0
        g_i = 0.0
        for x, y in zip(logits, ys):
            z = slope * x + intercept
            pred = 1.0 / (1.0 + math.exp(-z))
            err = pred - y
            g_s += err * x
            g_i += err
        slope -= learning_rate * (g_s / n)
        intercept -= learning_rate * (g_i / n)

    return {
        "method": "platt",
        "slope": round(slope, 6),
        "intercept": round(intercept, 6),
        "n": int(n),
    }


def expected_calibration_error(probs: list[float], outcomes: list[int], bins: int = 10) -> float:
    if not probs or len(probs) != len(outcomes):
        return 0.0
    total = len(probs)
    acc = 0.0
    for idx in range(bins):
        lo = idx / bins
        hi = 1.0 if idx == bins - 1 else (idx + 1) / bins
        bucket = [(p, y) for p, y in zip(probs, outcomes) if lo <= p < hi]
        if not bucket:
            continue
        pred_mean = sum(p for p, _ in bucket) / len(bucket)
        obs_mean = sum(y for _, y in bucket) / len(bucket)
        acc += abs(pred_mean - obs_mean) * (len(bucket) / total)
    return float(acc)


def brier_score(probs: list[float], outcomes: list[int]) -> float:
    if not probs or len(probs) != len(outcomes):
        return 0.0
    n = len(probs)
    return float(sum((p - y) ** 2 for p, y in zip(probs, outcomes)) / n)


def calibration_drift(
    *,
    current_metrics: dict[str, float],
    prior_metrics: dict[str, float] | None,
) -> dict[str, float | None]:
    if not prior_metrics:
        return {
            "ece_delta": None,
            "brier_delta": None,
        }
    return {
        "ece_delta": round(
            float(current_metrics.get("ece", 0.0)) - float(prior_metrics.get("ece", 0.0)),
            6,
        ),
        "brier_delta": round(
            float(current_metrics.get("brier", 0.0)) - float(prior_metrics.get("brier", 0.0)),
            6,
        ),
    }
