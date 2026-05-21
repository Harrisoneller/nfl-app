"""Probabilistic primitives for game predictions.

Turns a point estimate (expected margin / expected total) into a full outcome
distribution, and provides the proper scoring rules we grade ourselves with.
This is the shared engine behind the variance-first redesign (see
docs/PREDICTION_MODEL_SPEC.md): every game-level probability — win, cover,
over/under, credible interval — is derived from ONE margin distribution so the
numbers are mutually consistent.

NFL game margins are well-approximated by a Normal centered on the model's
expected margin with SD ~13.5 points, with extra probability mass landing on
key numbers (3, 7). We use the Normal as the engine for win/cover/total/interval
math; the key-number table is exposed separately for push probabilities, since
discretely modelling pushes inside the continuous CDF would be a second-order
correction that complicates the common case.

Pure-Python (math only) so it has no scientific-stack dependency and is trivial
to unit test.
"""
from __future__ import annotations

import math

# Historical game-to-game spreads. SD of final margin around its expectation is
# ~13.5; total points around their expectation ~10. Both are tunable and should
# be validated with the PIT histogram in the backtest (a U-shaped PIT means the
# SD is too small, a domed PIT means it's too large).
NFL_MARGIN_SIGMA = 13.5
NFL_TOTAL_SIGMA = 10.0

# Approximate empirical P(final margin == k) for NFL key numbers (regulation).
# Used only for push probabilities when a spread sits exactly on a key number.
# Source: long-run NFL margin frequencies; refine with our own data later.
KEY_NUMBER_PUSH: dict[int, float] = {
    3: 0.094, 7: 0.058, 6: 0.037, 10: 0.034, 4: 0.034,
    14: 0.025, 1: 0.027, 17: 0.013, 8: 0.024, 13: 0.012,
}

_SQRT2 = math.sqrt(2.0)
_SQRT_PI = math.sqrt(math.pi)
_INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)


# ---- Normal distribution primitives ---------------------------------------


def norm_cdf(z: float) -> float:
    """Standard normal CDF via the error function."""
    return 0.5 * (1.0 + math.erf(z / _SQRT2))


def norm_pdf(z: float) -> float:
    """Standard normal PDF."""
    return _INV_SQRT_2PI * math.exp(-0.5 * z * z)


def norm_ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam's rational approximation).

    Accurate to ~1e-9 over the open interval (0, 1). Clamped at the edges.
    """
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf

    # Coefficients
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]

    plow, phigh = 0.02425, 1.0 - 0.02425
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    if p > phigh:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
                ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)


# ---- Game-level probabilities (all derived from one margin distribution) ---


def win_prob(expected_margin: float, sigma: float = NFL_MARGIN_SIGMA) -> float:
    """P(home wins) = P(margin > 0) for margin ~ Normal(expected_margin, sigma)."""
    if sigma <= 0:
        return 1.0 if expected_margin > 0 else (0.0 if expected_margin < 0 else 0.5)
    return norm_cdf(expected_margin / sigma)


def cover_prob_home(expected_margin: float, home_line: float,
                    sigma: float = NFL_MARGIN_SIGMA) -> float:
    """P(home covers `home_line`), sportsbook convention (negative = home favored).

    Home covers iff actual margin > -home_line. Continuous approximation (ignores
    the discrete push mass — see KEY_NUMBER_PUSH / push_prob for that).
    """
    if sigma <= 0:
        return 1.0 if expected_margin > -home_line else 0.0
    return norm_cdf((expected_margin + home_line) / sigma)


def over_prob(expected_total: float, line: float,
              sigma: float = NFL_TOTAL_SIGMA) -> float:
    """P(combined points > line) for total ~ Normal(expected_total, sigma)."""
    if sigma <= 0:
        return 1.0 if expected_total > line else 0.0
    return norm_cdf((expected_total - line) / sigma)


def push_prob(home_line: float) -> float:
    """Approx P(spread pushes) when the line sits on an integer key number."""
    if abs(home_line - round(home_line)) > 1e-9:
        return 0.0  # half-point line — can't push
    return KEY_NUMBER_PUSH.get(abs(int(round(home_line))), 0.0)


def margin_interval(expected_margin: float, sigma: float = NFL_MARGIN_SIGMA,
                    level: float = 0.8) -> tuple[float, float]:
    """Central credible interval on the margin at the given probability level."""
    z = norm_ppf(0.5 + level / 2.0)
    return (expected_margin - z * sigma, expected_margin + z * sigma)


# ---- Proper scoring rules (for the backtest) -------------------------------


def crps_normal(expected: float, sigma: float, actual: float) -> float:
    """Closed-form CRPS for a Normal(expected, sigma) forecast vs `actual`.

    CRPS rewards a forecast that is both well-centered AND has the right spread,
    so it is the metric that tells us whether our margin SD is calibrated.
    Lower is better; in the same units as the observation (points).
    """
    if sigma <= 0:
        return abs(actual - expected)
    z = (actual - expected) / sigma
    return sigma * (z * (2.0 * norm_cdf(z) - 1.0) + 2.0 * norm_pdf(z) - 1.0 / _SQRT_PI)


def log_loss(prob: float, outcome: int, eps: float = 1e-12) -> float:
    """Binary log loss (a.k.a. cross-entropy) for one prediction.

    `outcome` is 1 if the event happened, else 0. Lower is better.
    """
    p = min(max(prob, eps), 1.0 - eps)
    return -(outcome * math.log(p) + (1 - outcome) * math.log(1.0 - p))


def brier(prob: float, outcome: int) -> float:
    """Brier score for one prediction: (prob - outcome)^2."""
    return (prob - outcome) ** 2
