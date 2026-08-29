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

    Football game outcomes are close enough to independent for a betting model; this
    deliberately ignores correlation (which mostly matters for same-game parlays).
    """
    out = 1.0
    for p in leg_probs:
        out *= max(0.0, min(1.0, p))
    return out


def expected_value(true_prob: float, decimal_odds: float) -> float:
    """EV per 1 unit staked: p * (dec - 1) - (1 - p). Positive = +EV."""
    return true_prob * (decimal_odds - 1.0) - (1.0 - true_prob)


def kelly_fraction(true_prob: float, decimal_odds: float, *, cap: float = 0.25) -> float:
    """Kelly-criterion bet fraction for a binary wager.

    f* = (bp - q) / b, where b = decimal_odds - 1, p = true_prob, q = 1 - p.
    Returns 0 when the bet is -EV (Kelly says don't bet). Capped at ``cap`` to
    discourage aggressive sizing on noisy estimates; default 25% of bankroll is
    a conservative ceiling that quants commonly call "quarter Kelly" practice.
    """
    b = decimal_odds - 1.0
    if b <= 0:
        return 0.0
    p = max(0.0, min(1.0, true_prob))
    q = 1.0 - p
    f = (b * p - q) / b
    if f <= 0:
        return 0.0
    return min(cap, f)


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


# --------------------------------------------------------------------------- #
# Sizing from a payoff distribution (parlays are not binary bets)
# --------------------------------------------------------------------------- #


def kelly_from_moments(
    expected_return: float,
    return_sd: float,
    *,
    cap: float = 0.02,
    fraction: float = 0.25,
) -> float:
    """Kelly stake for a wager whose gross return ``R`` is not binary.

    :func:`kelly_fraction` assumes exactly two outcomes. A parlay leg can push,
    which makes ``R`` a multi-valued random variable, and — more importantly —
    the binary formula fed a long-shot probability produces stakes that are
    wildly too large the moment that probability is even slightly optimistic.

    Maximizing ``E[log(1 - f + f R)]`` and expanding to second order gives

        f* = (E[R] - 1) / E[(R - 1)^2] = (E[R] - 1) / (Var[R] + (E[R] - 1)^2)

    which uses exactly the two moments :func:`correlation.price_parlay`
    already returns, and which automatically shrinks the stake as the payoff
    gets more dispersed — the behaviour full Kelly on a point estimate fails to
    produce.

    Defaults are deliberately conservative: quarter-Kelly, hard-capped at 2% of
    bankroll. A parlay's probability estimate is the product of N uncertain
    numbers, so even quarter-Kelly on the point estimate over-bets; the cap is
    what keeps a single optimistic slate from mattering.
    """
    excess = expected_return - 1.0
    if excess <= 0:
        return 0.0
    denom = return_sd * return_sd + excess * excess
    if denom <= 0:
        return 0.0
    return min(cap, max(0.0, fraction * excess / denom))


def devig_power(prices_american: list[int | float], *, tol: float = 1e-10) -> list[float]:
    """De-vig an n-way market by the **power** method rather than proportionally.

    Proportional de-vig (:func:`devig_two_way`) removes the same *fraction* of
    probability from every outcome, which systematically over-prices longshots:
    books do not spread their hold evenly, they load it onto the underdog. The
    power method solves for the exponent ``k`` with ``sum(p_i ** k) == 1``,
    which takes proportionally more from the low-probability side and matches
    observed closing lines noticeably better at long prices.

    This matters here specifically because parlay legs are frequently priced at
    long odds, where the two methods disagree by more than the edge we are
    trying to detect.
    """
    raw = [american_to_implied(p) for p in prices_american]
    total = sum(raw)
    if total <= 0:
        n = max(1, len(raw))
        return [1.0 / n] * len(raw)
    if abs(total - 1.0) < tol:
        return raw

    lo, hi = 0.05, 5.0
    for _ in range(200):
        k = 0.5 * (lo + hi)
        s = sum(p ** k for p in raw)
        if abs(s - 1.0) < tol:
            break
        if s > 1.0:
            lo = k
        else:
            hi = k
    k = 0.5 * (lo + hi)
    out = [p ** k for p in raw]
    z = sum(out)
    return [v / z for v in out] if z > 0 else raw
