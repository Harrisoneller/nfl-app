"""Attach calibrated uncertainty metadata to prediction payloads."""
from __future__ import annotations

from typing import Any

from . import prediction_dist


def calibration_score_from_ece(ece: float | None) -> float:
    """Map expected calibration error to [0, 1] where higher is better."""
    if ece is None:
        return 0.5
    return round(max(0.0, min(1.0, 1.0 - (ece * 2.0))), 3)


def confidence_tier(
    *,
    home_win_prob: float,
    interval_low: float,
    interval_high: float,
    calibration_score: float,
) -> str:
    spread = max(0.0, interval_high - interval_low)
    edge = abs(home_win_prob - 0.5)
    score = (edge * 1.6) + (calibration_score * 0.6) - (spread * 0.35)
    if score >= 0.65:
        return "high"
    if score >= 0.35:
        return "medium"
    return "low"


def attach_uncertainty(
    prediction: dict[str, Any],
    *,
    model_version: str,
    expected_calibration_error: float | None,
) -> dict[str, Any]:
    """Return prediction payload augmented with uncertainty and confidence labels."""
    dist = prediction.get("distribution") or {}
    expected_margin = float(dist.get("expected_margin", 0.0))
    lo, hi = prediction_dist.margin_interval(expected_margin, prediction_dist.NFL_MARGIN_SIGMA, 0.8)
    p_low = prediction_dist.win_prob(lo, prediction_dist.NFL_MARGIN_SIGMA)
    p_high = prediction_dist.win_prob(hi, prediction_dist.NFL_MARGIN_SIGMA)
    home_low, home_high = sorted((round(p_low, 3), round(p_high, 3)))
    away_low, away_high = round(1.0 - home_high, 3), round(1.0 - home_low, 3)
    cal_score = calibration_score_from_ece(expected_calibration_error)
    tier = confidence_tier(
        home_win_prob=float(prediction.get("home_win_prob", 0.5)),
        interval_low=home_low,
        interval_high=home_high,
        calibration_score=cal_score,
    )

    return {
        **prediction,
        "home_win_prob_interval_80": [home_low, home_high],
        "away_win_prob_interval_80": [away_low, away_high],
        "calibration_score": cal_score,
        "expected_calibration_error": expected_calibration_error,
        "confidence_tier": tier,
        "model_version": model_version,
    }
