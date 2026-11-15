"""Per-game closed-form normal CRPS.

``backend/scripts/backtest_v5.py`` prints RMSE, correlation, and ATS. It does
not score CRPS. The harness scores each game here, with the same formula as
``prediction_dist.crps_normal``. Sigma is the locked constant from ``specs``,
not a fitted residual SD.
"""
from __future__ import annotations

import math

import numpy as np

from app.services.prediction_dist import crps_normal

_SQRT2 = math.sqrt(2.0)
_SQRT_PI = math.sqrt(math.pi)
_INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)


def per_game_crps(expected, sigma: float, actual) -> np.ndarray:
    """CRPS of each game. Lower is better. Same units as the endpoint."""
    expected_arr = np.asarray(expected, dtype=float)
    actual_arr = np.asarray(actual, dtype=float)
    if expected_arr.shape != actual_arr.shape:
        raise ValueError("expected and actual must have the same shape")
    sig = float(sigma)
    if sig <= 0:
        return np.abs(actual_arr - expected_arr)
    z = (actual_arr - expected_arr) / sig
    erf = np.vectorize(math.erf, otypes=[float])
    cdf = 0.5 * (1.0 + erf(z / _SQRT2))
    pdf = _INV_SQRT_2PI * np.exp(-0.5 * z * z)
    return sig * (z * (2.0 * cdf - 1.0) + 2.0 * pdf - 1.0 / _SQRT_PI)


def crps_matches_reference(expected: float, sigma: float, actual: float) -> float:
    """Scalar wrapper around the production formula, for the parity test."""
    return float(crps_normal(expected, sigma, actual))
