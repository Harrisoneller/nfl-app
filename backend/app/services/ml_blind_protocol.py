"""Scoring rules for the market-blind NFL ML retrain.

Pure functions. Importing this module does not fit a model or read a season.
Gates use a season-week cluster bootstrap. The game bootstrap is descriptive.
"""
from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

import numpy as np

from . import prediction_dist, task_modeling_service
from .closing_line import LOST, WON, ats_grade
from .ml_blind_spec import (
    BOOTSTRAP_PERCENTILES,
    BOOTSTRAP_RESAMPLES,
    CLOSE_NI_UPPER,
    MDE_CRPS,
    POWER_MIN,
    SEED,
    SIGMA,
    Z_CRIT_95,
    Z_MIN,
    ats_cluster_lower_rate,
)


def bootstrap_mean_ci(
    samples: Sequence[float],
    *,
    seed: int = SEED,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> dict[str, float | int]:
    """90% paired interval of the mean. Percentiles 5 and 95, seed 20261026."""
    values = np.asarray(list(samples), dtype=float)
    if values.size == 0 or not np.isfinite(values).all():
        raise ValueError("bootstrap sample is empty or non-finite")
    rng = np.random.default_rng(seed)
    draw = rng.integers(0, values.size, size=(resamples, values.size))
    means = values[draw].mean(axis=1)
    lo, hi = np.percentile(means, list(BOOTSTRAP_PERCENTILES))
    return {
        "mean": float(values.mean()),
        "lo": float(lo),
        "hi": float(hi),
        "n": int(values.size),
        "resamples": int(resamples),
        "seed": int(seed),
        "role": "descriptive_game_bootstrap",
    }


def cluster_bootstrap_mean_ci(
    values: Sequence[float],
    clusters: Sequence[str],
    *,
    seed: int = SEED,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> dict[str, float | int]:
    """90% interval of the mean, resampling season-week clusters."""
    sample = np.asarray(list(values), dtype=float)
    labels = [str(cluster) for cluster in clusters]
    if sample.size == 0 or sample.size != len(labels) or not np.isfinite(sample).all():
        raise ValueError("cluster bootstrap sample is empty or misaligned")
    groups: dict[str, list[int]] = defaultdict(list)
    for idx, label in enumerate(labels):
        groups[label].append(idx)
    keys = list(groups)
    if len(keys) < 2:
        raise ValueError("cluster bootstrap needs at least two clusters")
    members = [np.asarray(groups[key], dtype=int) for key in keys]
    rng = np.random.default_rng(seed)
    means = np.empty(resamples, dtype=float)
    for draw_i in range(resamples):
        picked = rng.integers(0, len(members), size=len(members))
        idx = np.concatenate([members[i] for i in picked])
        means[draw_i] = sample[idx].mean()
    lo, hi = np.percentile(means, list(BOOTSTRAP_PERCENTILES))
    return {
        "mean": float(sample.mean()),
        "lo": float(lo),
        "hi": float(hi),
        "n": int(sample.size),
        "n_clusters": len(keys),
        "resamples": int(resamples),
        "seed": int(seed),
        "role": "gate_cluster_bootstrap",
    }


def cluster_se_of_mean(values: Sequence[float], clusters: Sequence[str]) -> float:
    """Cluster-robust SE of the mean. Clusters are season-week."""
    sample = np.asarray(list(values), dtype=float)
    labels = [str(cluster) for cluster in clusters]
    if sample.size == 0 or sample.size != len(labels) or not np.isfinite(sample).all():
        raise ValueError("cluster SE sample is empty or misaligned")
    groups: dict[str, list[float]] = defaultdict(list)
    for value, label in zip(sample, labels, strict=True):
        groups[label].append(float(value))
    n_clusters = len(groups)
    if n_clusters < 2:
        raise ValueError("cluster SE needs at least two clusters")
    n = float(sample.size)
    mean = float(sample.mean())
    score = 0.0
    for rows in groups.values():
        total = float(sum(rows))
        score += (total - len(rows) * mean) ** 2
    return math.sqrt((n_clusters / (n_clusters - 1)) * score) / n


def selection_z(mean_diff: float, se: float, delta: float = MDE_CRPS) -> float:
    """z = (non-inferiority margin − estimate) / cluster SE. Estimate is candidate minus close."""
    if se <= 0 or not math.isfinite(se):
        raise ValueError("selection SE must be positive")
    return (delta - mean_diff) / se


def projected_se(se_selection: float, n_clusters_selection: int, n_clusters_final: int) -> float:
    """Scale the selection SE by the ratio of cluster counts. No outcomes are read."""
    if se_selection <= 0 or n_clusters_selection < 2 or n_clusters_final < 2:
        raise ValueError("projected SE needs a positive selection SE and two clusters on each side")
    return se_selection * math.sqrt(n_clusters_selection / n_clusters_final)


def projected_power(se_final: float, delta: float = MDE_CRPS) -> float:
    """Normal power of the one-sided NI test at a true difference of zero.

    The critical value is the 95th percentile. Power is at least 0.5 when the
    projected SE is small enough that zero sits at least that many SEs below the margin.
    """
    if se_final <= 0:
        raise ValueError("final SE must be positive")
    return float(prediction_dist.norm_cdf(delta / se_final - Z_CRIT_95))


def selection_opens_final(mean_diff: float, se: float, se_final: float) -> dict[str, float | bool]:
    """Both z and projected power must clear their floors. This does not score a final season."""
    z_value = selection_z(mean_diff, se)
    power = projected_power(se_final)
    return {
        "z": z_value,
        "power": power,
        "open_final": bool(z_value >= Z_MIN and power >= POWER_MIN),
    }


def inner_oof_plan(seasons: Sequence[int]) -> list[tuple[int, tuple[int, ...]]]:
    """Expanding inner seasons for Platt. The earliest season has no prior fold.

    A calibration season is never included in its own training seasons.
    """
    ordered = tuple(sorted({int(season) for season in seasons}))
    plan: list[tuple[int, tuple[int, ...]]] = []
    for idx, cal_season in enumerate(ordered):
        prior = ordered[:idx]
        if prior:
            plan.append((cal_season, prior))
    return plan


def grade_predictions(frame: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Grade aligned candidate and incumbent rows.

    Each row needs ``actual_margin``, ``candidate_margin``, ``incumbent_margin``,
    ``candidate_prob``, ``incumbent_prob``, and ``cluster`` (season-week).
    ``close`` is the nflverse home margin. Sigma is the scoring convention.
    """
    crps_gain: list[float] = []
    brier_gain: list[float] = []
    logloss_gain: list[float] = []
    cand_probs: list[float] = []
    inc_probs: list[float] = []
    outcomes: list[int] = []
    close_crps: list[float] = []
    close_diff: list[float] = []
    close_clusters: list[str] = []
    cover_diff: list[float] = []
    cover_clusters: list[str] = []
    candidate_bits: list[float] = []
    incumbent_bits: list[float] = []
    for row in frame:
        actual = float(row["actual_margin"])
        cand_m = float(row["candidate_margin"])
        inc_m = float(row["incumbent_margin"])
        cand_p = float(row["candidate_prob"])
        inc_p = float(row["incumbent_prob"])
        y = 1 if actual > 0 else 0
        cluster = str(row["cluster"])
        crps_gain.append(
            prediction_dist.crps_normal(inc_m, SIGMA, actual)
            - prediction_dist.crps_normal(cand_m, SIGMA, actual)
        )
        brier_gain.append((inc_p - y) ** 2 - (cand_p - y) ** 2)
        logloss_gain.append(
            prediction_dist.log_loss(inc_p, y) - prediction_dist.log_loss(cand_p, y)
        )
        cand_probs.append(cand_p)
        inc_probs.append(inc_p)
        outcomes.append(y)
        close = row.get("close")
        if close is not None and math.isfinite(float(close)):
            close_value = float(close)
            close_score = prediction_dist.crps_normal(close_value, SIGMA, actual)
            cand_score = prediction_dist.crps_normal(cand_m, SIGMA, actual)
            close_crps.append(close_score)
            close_diff.append(cand_score - close_score)
            close_clusters.append(cluster)
            bits = _cover_bits(cand_m, inc_m, close_value, actual)
            if bits is not None:
                cand_bit, inc_bit = bits
                candidate_bits.append(cand_bit)
                incumbent_bits.append(inc_bit)
                cover_diff.append(cand_bit - inc_bit)
                cover_clusters.append(cluster)
    crps = bootstrap_mean_ci(crps_gain)
    brier = bootstrap_mean_ci(brier_gain)
    logloss = bootstrap_mean_ci(logloss_gain)
    ats = _ats_summary(cover_diff, cover_clusters, candidate_bits, incumbent_bits)
    close_ni = _close_ni(close_diff, close_clusters)
    summary = {
        "n_games": len(crps_gain),
        "sigma": SIGMA,
        "sigma_role": "scoring_convention_not_production_distribution",
        "crps_gain_vs_incumbent": crps,
        "brier_gain": brier,
        "logloss_gain": logloss,
        "ats": ats,
        "close_ni": close_ni,
        "candidate_ece": task_modeling_service.expected_calibration_error(cand_probs, outcomes),
        "incumbent_ece": task_modeling_service.expected_calibration_error(inc_probs, outcomes),
        "close_crps": float(np.mean(close_crps)) if close_crps else None,
        "enables_ml": False,
        "merge_authorized": False,
        "ship_means": "review_only",
    }
    summary["verdict"] = verdict(summary)
    return summary


def verdict(summary: dict[str, Any]) -> str:
    """Close-NI failure is a hard no-ship. ATS failure is a no-ship.

    A SHIP token only marks the result eligible for QA review. It does not
    enable the ML layer and it does not authorize a merge.
    """
    close_ci = (summary.get("close_ni") or {}).get("ci") or {}
    ats_ci = (summary.get("ats") or {}).get("cluster_ci") or {}
    close_hi = close_ci.get("hi")
    ats_lo = ats_ci.get("lo")
    if close_hi is None or float(close_hi) > CLOSE_NI_UPPER:
        return "NO-SHIP"
    if ats_lo is None or float(ats_lo) < ats_cluster_lower_rate():
        return "NO-SHIP"
    return "SHIP"


def _close_ni(diffs: list[float], clusters: list[str]) -> dict[str, Any]:
    if len(diffs) < 2 or len(set(clusters)) < 2:
        return {"n": len(diffs), "diff": None, "ci": None}
    return {
        "n": len(diffs),
        "diff": float(np.mean(diffs)),
        "ci": cluster_bootstrap_mean_ci(diffs, clusters),
    }


def _ats_summary(
    cover_diff: list[float],
    clusters: list[str],
    candidate_bits: list[float],
    incumbent_bits: list[float],
) -> dict[str, Any]:
    if len(cover_diff) < 2 or len(set(clusters)) < 2:
        return {
            "n": len(cover_diff),
            "diff": None if not cover_diff else float(np.mean(cover_diff)),
            "candidate_rate": None if not candidate_bits else float(np.mean(candidate_bits)),
            "incumbent_rate": None if not incumbent_bits else float(np.mean(incumbent_bits)),
            "cluster_ci": None,
            "game_bootstrap": None,
        }
    return {
        "n": len(cover_diff),
        "diff": float(np.mean(cover_diff)),
        "candidate_rate": float(np.mean(candidate_bits)),
        "incumbent_rate": float(np.mean(incumbent_bits)),
        "cluster_ci": cluster_bootstrap_mean_ci(cover_diff, clusters),
        "game_bootstrap": bootstrap_mean_ci(cover_diff),
    }


def _cover_bits(
    candidate_margin: float,
    incumbent_margin: float,
    close: float,
    actual: float,
) -> tuple[float, float] | None:
    """Cover bits on games where both models have a bet. Pushes and no-bets drop out."""
    candidate = ats_grade(candidate_margin, close, actual)
    incumbent = ats_grade(incumbent_margin, close, actual)
    if candidate not in (WON, LOST) or incumbent not in (WON, LOST):
        return None
    return (1.0 if candidate == WON else 0.0, 1.0 if incumbent == WON else 0.0)
