"""Scoring rules for the market-blind NFL ML retrain.

Pure functions. Importing this module does not fit a model or read a season.
The evaluation script must refuse to call into a holdout until QA sets the lock.
"""
from __future__ import annotations

import math
import os
from collections.abc import Sequence
from typing import Any

import numpy as np

from . import prediction_dist, task_modeling_service
from .closing_line import LOST, WON, ats_grade
from .ml_blind_spec import (
    ATS_NONINFERIORITY,
    BOOTSTRAP_PERCENTILES,
    BOOTSTRAP_RESAMPLES,
    QA_LOCK_ENV,
    QA_LOCK_VALUE,
    SEED,
    SIGMA,
)


def require_qa_lock() -> None:
    """Stop the process unless QA has released the single holdout look."""
    if os.environ.get(QA_LOCK_ENV) != QA_LOCK_VALUE:
        raise SystemExit(
            "refusing to score: set NFL_ML_BLIND_EVAL_CONFIRM=QA-LOCK after the prereg is locked"
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
    }


def grade_predictions(frame: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Grade aligned candidate and incumbent rows.

    Each row needs ``actual_margin``, ``candidate_margin``, ``incumbent_margin``,
    ``candidate_prob``, and ``incumbent_prob``. ``close`` is optional and is used
    only for ATS. Positive margins favor the home team. ``close`` uses that same
    sign (nflverse ``spread_line``).
    """
    crps_gain: list[float] = []
    brier_gain: list[float] = []
    logloss_gain: list[float] = []
    cand_probs: list[float] = []
    inc_probs: list[float] = []
    outcomes: list[int] = []
    close_crps: list[float] = []
    cover_diff: list[float] = []
    candidate_bits: list[float] = []
    incumbent_bits: list[float] = []
    for row in frame:
        actual = float(row["actual_margin"])
        cand_m = float(row["candidate_margin"])
        inc_m = float(row["incumbent_margin"])
        cand_p = float(row["candidate_prob"])
        inc_p = float(row["incumbent_prob"])
        y = 1 if actual > 0 else 0
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
            close_crps.append(prediction_dist.crps_normal(float(close), SIGMA, actual))
            bits = _cover_bits(cand_m, inc_m, float(close), actual)
            if bits is not None:
                cand_bit, inc_bit = bits
                candidate_bits.append(cand_bit)
                incumbent_bits.append(inc_bit)
                cover_diff.append(cand_bit - inc_bit)
    crps = bootstrap_mean_ci(crps_gain)
    brier = bootstrap_mean_ci(brier_gain)
    logloss = bootstrap_mean_ci(logloss_gain)
    ats = _ats_summary(cover_diff, candidate_bits, incumbent_bits)
    summary = {
        "n_games": len(crps_gain),
        "sigma": SIGMA,
        "crps_gain": crps,
        "brier_gain": brier,
        "logloss_gain": logloss,
        "ats": ats,
        "candidate_ece": task_modeling_service.expected_calibration_error(cand_probs, outcomes),
        "incumbent_ece": task_modeling_service.expected_calibration_error(inc_probs, outcomes),
        "close_crps": float(np.mean(close_crps)) if close_crps else None,
        "crps_beats": crps["lo"] > 0,
        "brier_beats": brier["lo"] > 0,
        "logloss_beats": logloss["lo"] > 0,
    }
    summary["verdict"] = verdict(summary)
    return summary


def verdict(summary: dict[str, Any]) -> str:
    """SHIP only when every point-estimate gate passes. Beats-claims are not required."""
    ats_diff = summary["ats"]["diff"]
    if ats_diff is None:
        return "NO-SHIP"
    ok = (
        float(summary["crps_gain"]["mean"]) >= 0.0
        and float(summary["brier_gain"]["mean"]) >= 0.0
        and float(summary["logloss_gain"]["mean"]) >= 0.0
        and float(ats_diff) >= ATS_NONINFERIORITY
    )
    return "SHIP" if ok else "NO-SHIP"


def _ats_summary(
    cover_diff: list[float],
    candidate_bits: list[float],
    incumbent_bits: list[float],
) -> dict[str, Any]:
    if not cover_diff:
        return {
            "n": 0,
            "diff": None,
            "candidate_rate": None,
            "incumbent_rate": None,
            "ci": None,
        }
    return {
        "n": len(cover_diff),
        "diff": float(np.mean(cover_diff)),
        "candidate_rate": float(np.mean(candidate_bits)),
        "incumbent_rate": float(np.mean(incumbent_bits)),
        "ci": bootstrap_mean_ci(cover_diff),
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
