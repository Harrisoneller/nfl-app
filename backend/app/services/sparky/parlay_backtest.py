"""Does the parlay engine actually work? The evidence layer.

The existing backtest reports one parlay number — "rank #1 hit rate" — which
cannot answer the only question that matters. A 12% hit rate is excellent on a
+900 ticket and catastrophic on a +250 one, and neither tells you whether the
model's *probability* was right. This module measures the three things that do:

1. **ROI at the suggested stake.** Flat-stake ROI flatters longshots for the
   same reason ranking by EV% does. The engine sizes with fractional Kelly, so
   the honest question is what a bankroll following those stakes would have
   done. Both are reported, plus the realized log-growth.

2. **Reliability of the parlay probability itself.** Bucket tickets by
   predicted hit probability and compare to the realized rate, with Wilson
   intervals. This is the single most diagnostic output in the module: if
   predicted 20% tickets cash 12% of the time, the leg probabilities are
   optimistic and ``parlay.selection_kappa`` (or ``parlay.edge_lambda``) is
   wrong — and the size of the gap says by how much.

3. **Attribution against the naive engine.** Every ticket is priced three ways
   — naive independent product, correlation-aware, and correlation-aware with
   the selection penalty — and scored against what actually happened. That
   isolates whether each layer of the rebuild earned its complexity, rather
   than assuming it did.

A note on what this cannot tell you
-----------------------------------
Parlays are low-count, high-variance events. Fifty settled tickets tell you
almost nothing about ROI; the reliability curve and the leg-level Brier score
converge far faster because they use every leg, not every ticket. Read those
first, and treat ROI as the slowest-moving evidence on the page. The Wilson
intervals are reported precisely so a good-looking point estimate on twelve
tickets cannot be mistaken for a result.

Pure module: settled rows are passed in as plain dicts.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .odds_math import clamp

WIN, PUSH, LOSS = "win", "push", "loss"


# --------------------------------------------------------------------------- #
# Wilson interval
# --------------------------------------------------------------------------- #


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval — correct at the small counts parlays actually produce.

    The normal approximation degenerates badly near 0 and 1 and at small n,
    which is exactly the regime a parlay hit rate lives in. Using it would
    routinely produce intervals containing negative probabilities.
    """
    if n <= 0:
        return 0.0, 1.0
    p = successes / n
    d = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return clamp(centre - half, 0.0, 1.0), clamp(centre + half, 0.0, 1.0)


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #


@dataclass
class SettledTicket:
    """One graded ticket.

    ``gross_return`` is the payout multiple actually realized (0.0 on a loss,
    1.0 if every leg pushed, the product of the surviving legs' decimal odds
    otherwise) — not the ticket's advertised price, because pushes change it.
    """

    slate_date: str
    n_legs: int
    outcome: str                      # win | push | loss
    gross_return: float
    predicted_hit_prob: float         # engine's push-aware P(does not lose)
    predicted_ev: float               # before the selection penalty
    predicted_ev_adjusted: float      # after it
    naive_hit_prob: float             # what independent multiplication said
    kelly_fraction: float
    implied_prob: float               # what the price said
    markets: tuple[str, ...] = ()


@dataclass
class ParlayBacktestReport:
    n_tickets: int
    n_legs_total: int
    roi_flat: float | None
    roi_kelly: float | None
    log_growth: float | None
    hit_rate: float | None
    hit_rate_ci: tuple[float, float] | None
    predicted_hit_rate: float | None
    calibration_gap: float | None       # predicted minus realized, in points
    reliability: list[dict[str, Any]] = field(default_factory=list)
    by_leg_count: list[dict[str, Any]] = field(default_factory=list)
    attribution: dict[str, Any] = field(default_factory=dict)
    verdict: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "n_tickets": self.n_tickets,
            "n_legs_total": self.n_legs_total,
            "roi_flat": _r(self.roi_flat, 4),
            "roi_kelly": _r(self.roi_kelly, 6),
            "log_growth": _r(self.log_growth, 6),
            "hit_rate": _r(self.hit_rate, 4),
            "hit_rate_ci": (
                [round(self.hit_rate_ci[0], 4), round(self.hit_rate_ci[1], 4)]
                if self.hit_rate_ci else None
            ),
            "predicted_hit_rate": _r(self.predicted_hit_rate, 4),
            "calibration_gap": _r(self.calibration_gap, 4),
            "reliability": self.reliability,
            "by_leg_count": self.by_leg_count,
            "attribution": self.attribution,
            "verdict": self.verdict,
        }


def _r(v: float | None, n: int) -> float | None:
    return None if v is None else round(v, n)


# --------------------------------------------------------------------------- #
# The report
# --------------------------------------------------------------------------- #

_BINS = ((0.0, 0.02), (0.02, 0.05), (0.05, 0.10), (0.10, 0.20),
         (0.20, 0.35), (0.35, 0.55), (0.55, 1.01))


def evaluate(tickets: list[SettledTicket]) -> ParlayBacktestReport:
    """Score a set of settled tickets."""
    graded = [t for t in tickets if t.outcome in (WIN, PUSH, LOSS)]
    n = len(graded)
    if n == 0:
        return ParlayBacktestReport(
            n_tickets=0, n_legs_total=0, roi_flat=None, roi_kelly=None,
            log_growth=None, hit_rate=None, hit_rate_ci=None,
            predicted_hit_rate=None, calibration_gap=None,
            verdict="No settled tickets yet — nothing can be concluded.",
        )

    hits = sum(1 for t in graded if t.outcome != LOSS)
    hit_rate = hits / n
    predicted = sum(t.predicted_hit_prob for t in graded) / n

    # Flat stake: 1 unit per ticket.
    roi_flat = sum(t.gross_return - 1.0 for t in graded) / n

    # Kelly: stake each ticket at its suggested fraction of a running bankroll.
    bankroll = 1.0
    staked = 0.0
    returned = 0.0
    for t in graded:
        f = clamp(t.kelly_fraction, 0.0, 0.5)
        if f <= 0:
            continue
        stake = bankroll * f
        payout = stake * t.gross_return
        staked += stake
        returned += payout
        bankroll = bankroll - stake + payout
    roi_kelly = (returned - staked) / staked if staked > 0 else None
    log_growth = math.log(bankroll) / n if bankroll > 0 and staked > 0 else None

    # --- reliability ------------------------------------------------------- #
    reliability: list[dict[str, Any]] = []
    for lo, hi in _BINS:
        bucket = [t for t in graded if lo <= t.predicted_hit_prob < hi]
        if not bucket:
            continue
        b_hits = sum(1 for t in bucket if t.outcome != LOSS)
        ci = wilson(b_hits, len(bucket))
        pred = sum(t.predicted_hit_prob for t in bucket) / len(bucket)
        reliability.append({
            "bin": f"{lo * 100:.0f}-{hi * 100:.0f}%",
            "n": len(bucket),
            "predicted": round(pred, 4),
            "realized": round(b_hits / len(bucket), 4),
            "ci_low": round(ci[0], 4),
            "ci_high": round(ci[1], 4),
            # True when the engine's own prediction sits outside the realized
            # interval — i.e. the miss is bigger than sampling noise explains.
            "significant_miss": not (ci[0] <= pred <= ci[1]),
        })

    # --- by leg count ------------------------------------------------------ #
    by_leg: list[dict[str, Any]] = []
    for k in sorted({t.n_legs for t in graded}):
        sub = [t for t in graded if t.n_legs == k]
        k_hits = sum(1 for t in sub if t.outcome != LOSS)
        by_leg.append({
            "n_legs": k,
            "n": len(sub),
            "hit_rate": round(k_hits / len(sub), 4),
            "predicted": round(sum(t.predicted_hit_prob for t in sub) / len(sub), 4),
            "roi_flat": round(sum(t.gross_return - 1.0 for t in sub) / len(sub), 4),
        })

    # --- attribution ------------------------------------------------------- #
    # Brier score of the ticket-level probability under each pricing scheme.
    # Lower is better; the comparison is what says whether the extra machinery
    # bought anything, rather than merely being more sophisticated.
    def _brier(get: Any) -> float:
        return sum((get(t) - (0.0 if t.outcome == LOSS else 1.0)) ** 2 for t in graded) / n

    brier_model = _brier(lambda t: t.predicted_hit_prob)
    brier_naive = _brier(lambda t: t.naive_hit_prob)
    brier_market = _brier(lambda t: t.implied_prob)

    attribution = {
        "brier_engine": round(brier_model, 5),
        "brier_naive_independent": round(brier_naive, 5),
        "brier_market_price": round(brier_market, 5),
        "correlation_layer_gain": round(brier_naive - brier_model, 5),
        "beats_market_price": brier_model < brier_market,
        "mean_predicted_ev": round(
            sum(t.predicted_ev for t in graded) / n, 5),
        "mean_predicted_ev_adjusted": round(
            sum(t.predicted_ev_adjusted for t in graded) / n, 5),
        "realized_ev": round(roi_flat, 5),
        # If realized ROI sits below the *adjusted* EV the engine promised, the
        # selection penalty is too small. This is the number that calibrates
        # `parlay.selection_kappa`.
        "ev_shortfall_vs_adjusted": round(
            roi_flat - (sum(t.predicted_ev_adjusted for t in graded) / n), 5),
    }

    gap = predicted - hit_rate
    ci = wilson(hits, n)
    return ParlayBacktestReport(
        n_tickets=n,
        n_legs_total=sum(t.n_legs for t in graded),
        roi_flat=roi_flat,
        roi_kelly=roi_kelly,
        log_growth=log_growth,
        hit_rate=hit_rate,
        hit_rate_ci=ci,
        predicted_hit_rate=predicted,
        calibration_gap=gap,
        reliability=reliability,
        by_leg_count=by_leg,
        attribution=attribution,
        verdict=_verdict(n, gap, ci, predicted, roi_flat, attribution),
    )


def _verdict(
    n: int,
    gap: float,
    ci: tuple[float, float],
    predicted: float,
    roi_flat: float,
    attribution: dict[str, Any],
) -> str:
    """A blunt one-liner, because a wall of statistics is easy to read hopefully."""
    if n < 30:
        return (
            f"Only {n} settled tickets — far too few to conclude anything about ROI. "
            f"Watch the reliability curve and leg-level scores, which converge much faster."
        )
    if not (ci[0] <= predicted <= ci[1]):
        direction = "optimistic" if gap > 0 else "pessimistic"
        return (
            f"Ticket probabilities are {direction} by {abs(gap) * 100:.1f} points and the "
            f"miss is larger than sampling noise (predicted {predicted * 100:.1f}%, "
            f"realized {ci[0] * 100:.1f}-{ci[1] * 100:.1f}%). "
            + (
                "Raise parlay.selection_kappa or lower parlay.edge_lambda."
                if gap > 0 else
                "The engine is leaving edge on the table; the shrinkage is too aggressive."
            )
        )
    shortfall = attribution.get("ev_shortfall_vs_adjusted", 0.0)
    if roi_flat > 0 and shortfall > -0.02:
        return (
            f"Calibrated and profitable on {n} tickets: {roi_flat * 100:+.1f}% flat ROI "
            f"against {attribution['mean_predicted_ev_adjusted'] * 100:+.1f}% promised. "
            f"Still a small sample — keep watching."
        )
    if roi_flat <= 0:
        return (
            f"Probabilities are calibrated but flat ROI is {roi_flat * 100:+.1f}% on "
            f"{n} tickets. Either variance, or the edge is real but too thin to clear "
            f"the price. Check whether the winning legs are concentrated in one market."
        )
    return f"Calibrated on {n} tickets; ROI {roi_flat * 100:+.1f}%."
