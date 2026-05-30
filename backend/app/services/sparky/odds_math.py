"""Odds math — the universal-engine primitives (SOW 1, "Required Calculations").

All functions are pure. American odds are integers (e.g. -150, +130); decimal
odds are floats >= 1.0; probabilities are floats in [0, 1].

Conventions
-----------
- A negative american price (-150) is a favorite: risk 150 to win 100.
- A positive american price (+130) is an underdog: risk 100 to win 130.
- "Implied probability" from a single price includes the book's vig (margin),
  so a game's two implied probs sum to > 1.0. "De-vigged" (a.k.a. no-vig /
  fair) probabilities normalize that pair back to 1.0.
"""
from __future__ import annotations

import math

# --------------------------------------------------------------------------- #
# Single-price conversions
# --------------------------------------------------------------------------- #


def american_to_decimal(price: int | float) -> float:
    """Convert American odds to decimal odds (total return per 1 unit staked)."""
    p = float(price)
    if p == 0:
        raise ValueError("American odds cannot be 0")
    if p > 0:
        return 1.0 + p / 100.0
    return 1.0 + 100.0 / (-p)


def decimal_to_american(decimal_odds: float) -> int:
    """Convert decimal odds back to the nearest American integer price."""
    if decimal_odds <= 1.0:
        raise ValueError("Decimal odds must be > 1.0")
    if decimal_odds >= 2.0:
        return int(round((decimal_odds - 1.0) * 100.0))
    return int(round(-100.0 / (decimal_odds - 1.0)))


def american_to_implied(price: int | float) -> float:
    """Implied win probability from a single American price (vig included)."""
    p = float(price)
    if p < 0:
        return (-p) / ((-p) + 100.0)
    return 100.0 / (p + 100.0)


def implied_to_american(prob: float) -> int:
    """Inverse of :func:`american_to_implied` for a probability in (0, 1)."""
    if not 0.0 < prob < 1.0:
        raise ValueError("Probability must be strictly between 0 and 1")
    if prob >= 0.5:
        return int(round(-(prob / (1.0 - prob)) * 100.0))
    return int(round(((1.0 - prob) / prob) * 100.0))


def decimal_to_implied(decimal_odds: float) -> float:
    """Implied probability from decimal odds (vig included)."""
    if decimal_odds <= 1.0:
        raise ValueError("Decimal odds must be > 1.0")
    return 1.0 / decimal_odds


# --------------------------------------------------------------------------- #
# Two-way de-vig
# --------------------------------------------------------------------------- #


def devig_two_way(home_price: int | float, away_price: int | float) -> tuple[float, float]:
    """Return fair (no-vig) win probabilities for a two-outcome market.

    Normalizes the two vig-included implied probabilities so they sum to 1.0
    (the "multiplicative"/proportional method, the standard for two-way books).
    """
    h = american_to_implied(home_price)
    a = american_to_implied(away_price)
    total = h + a
    if total <= 0:
        return 0.5, 0.5
    return h / total, a / total


def vig_from_prices(home_price: int | float, away_price: int | float) -> float:
    """The book's hold (overround) on a two-way market, as a fraction.

    e.g. two -110 sides imply 0.524 + 0.524 = 1.048 -> ~4.8% vig.
    """
    return american_to_implied(home_price) + american_to_implied(away_price) - 1.0


# --------------------------------------------------------------------------- #
# Parlay combination
# --------------------------------------------------------------------------- #


def parlay_decimal(prices_american: list[int | float]) -> float:
    """Decimal odds for a parlay = product of each leg's decimal odds."""
    out = 1.0
    for p in prices_american:
        out *= american_to_decimal(p)
    return out


def parlay_american(prices_american: list[int | float]) -> int:
    """American odds for a parlay (rounded to the nearest integer price)."""
    return decimal_to_american(parlay_decimal(prices_american))


def parlay_implied(prices_american: list[int | float]) -> float:
    """Implied (vig-included) probability that the whole parlay cashes."""
    return decimal_to_implied(parlay_decimal(prices_american))


def combined_true_prob(leg_probs: list[float]) -> float:
    """Model probability the parlay hits = product of independent leg probs.

    NFL game outcomes are close enough to independent for a betting model; this
    deliberately ignores correlation (which mostly matters for same-game parlays).
    """
    out = 1.0
    for p in leg_probs:
        out *= max(0.0, min(1.0, p))
    return out


def expected_value(true_prob: float, decimal_odds: float) -> float:
    """EV per 1 unit staked: p * (dec - 1) - (1 - p). Positive = +EV."""
    return true_prob * (decimal_odds - 1.0) - (1.0 - true_prob)


def edge(true_prob: float, market_implied: float) -> float:
    """Model edge = our probability minus the market's (de-vigged) probability."""
    return true_prob - market_implied


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def logit(p: float) -> float:
    p = clamp(p, 1e-6, 1 - 1e-6)
    return math.log(p / (1.0 - p))


def inv_logit(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))
