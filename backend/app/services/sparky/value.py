"""Bet quality: turning a priceable leg into a decision.

Why this module exists
----------------------
Sparky's dashboard used to rank the slate by ``confidence_score``. Confidence is
highest exactly where the model and the market agree, which is where there is no
money left, so the board filled up with 90%-to-win chalk labelled "anchor". A
-2000 moneyline is not an edge; it is a 20-unit risk to win one, on a number the
market prices better than we do.

The fix is not a cosmetic filter. It is to score each leg on the only three
questions that decide whether a bet is worth making:

1. **Is it +EV at the price we can actually get?**  ``expected_value`` on the
   :class:`~app.services.sparky.legs.LegCandidate`, computed against the *best*
   available price and the *consensus* fair probability.
2. **How likely is that +EV to be real rather than estimation noise?**
   :attr:`ValuePick.p_edge_real` — the probability our true win probability
   exceeds the break-even probability, given the residual uncertainty in a
   shrunk leg estimate.
3. **What does betting it do to the bankroll?**  Expected log-growth, and the
   fractional-Kelly stake that achieves it. This orders the survivors; it is
   explicitly *not* what rejects chalk (see :func:`growth_rate`).

Why (2) is the piece that kills chalk
-------------------------------------
Break-even probability at -2000 is 0.9524. To know the sign of the EV there, we
need our probability estimate to be accurate to well under a point, at the exact
end of the curve where the model has the least resolution (starters pulled,
garbage time, backdoor covers) and where the de-vig is least trustworthy (books
load hold onto the longshot). A global logit-scale ``tau`` fitted mostly on
competitive games badly understates that error, and taken at face value it
reports a 1-point chalk edge as a near-certainty.

Two corrections, applied together:

- :mod:`shrinkage` now fits the trust factor **per probability band**, so a
  chalk edge is shrunk by what chalk history supports rather than by what the
  middle of the board supports.
- ``tau`` is inflated toward the tails here, by a factor that is zero at a
  pick'em and grows with the square of the distance from it. Justification is
  the same asymmetry as above: our uncertainty about a 95% number is not the
  same size as our uncertainty about a 55% number, and on the log-odds scale a
  constant ``tau`` claims that it is.

Both are exposed as tunables and both self-correct: as settled history
accumulates, the fitted per-band lambda replaces the conservative prior, and if
the model really does beat the closing line on chalk, chalk comes back.

Pure module: no DB, no network, no third-party imports.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import odds_math
from .legs import MARKET_MONEYLINE, MARKET_SPREAD, MARKET_TOTAL, LegCandidate
from .odds_math import clamp
from .shrinkage import logit

# --------------------------------------------------------------------------- #
# Defaults (import-safe; live values arrive from the param registry via the
# service layer, per this codebase's "never read params at import time" rule)
# --------------------------------------------------------------------------- #

#: Minimum EV per unit staked for a leg to be called playable. 2% is not
#: arbitrary: the fitted lambda's own standard error on a few hundred settled
#: picks moves a leg's edge by roughly a point of probability, which at typical
#: -110 pricing is about 2% of EV. Below that we cannot distinguish a real edge
#: from the error bar on our estimate of how real our edges are.
DEFAULT_MIN_EV = 0.02

#: Minimum P(the bet is actually +EV) for a leg to be called playable.
DEFAULT_MIN_P_EDGE_REAL = 0.58

#: How hard to inflate residual uncertainty toward the tails. At the default,
#: a leg priced at a fair 95% carries ~2.3x the logit-scale uncertainty of a
#: pick'em. Set to 0 to reproduce the flat-tau behaviour exactly.
DEFAULT_TAIL_TAU_INFLATION = 1.5

#: Moneylines shorter than this are never *recommended*. They are still priced
#: and still shown, in a separate "priced out" section with the reason attached,
#: because silently dropping data is how a tool loses trust. The number is a
#: product decision, not a model output: at -300 a bet must win 75% of the time
#: to break even, books cut limits hard on that side, and a single bad estimate
#: costs three times what a good one wins.
DEFAULT_ML_PRICE_FLOOR = -250

#: Longshot ceiling for the board. Past here the de-vig is dominated by how the
#: book distributes its hold rather than by what it thinks will happen.
DEFAULT_MAX_PRICE = 600

#: Minimum books quoting a leg. One book's "consensus" is one book's opinion
#: plus one book's hold.
DEFAULT_MIN_BOOKS = 3

#: Stakes below this (in units, where 1u = 1% of bankroll) are not worth the
#: transaction. A pick that sizes to 0.05u is telling you not to bet it.
DEFAULT_MIN_STAKE_UNITS = 0.15

#: Fraction of full Kelly, and the hard cap as a fraction of bankroll.
DEFAULT_KELLY_FRACTION = 0.25
DEFAULT_KELLY_CAP = 0.03

#: Largest raw model-vs-market disagreement, on the log-odds scale, that is
#: treated as an opinion rather than a fault.
#:
#: This is a circuit breaker, not a tuning knob. 1.0 logits is roughly 52% -> 74%
#: — about seven points of spread. Nobody beats a multi-book consensus by seven
#: points. A gap that size is a broken input: a stale distribution, a mismatched
#: line (our -6.5 against the book's +6.5), a total whose sigma collapsed, a
#: team the model has no rating for. Every one of those has happened in
#: this codebase, and each would otherwise surface as a maximum-stake "strong"
#: recommendation with 100% confidence attached — the single most expensive way
#: for this page to be wrong.
#:
#: Measured on the *raw* calibrated model probability, before shrinkage: after
#: shrinkage a fault has already been pulled most of the way onto the market and
#: looks merely optimistic. The point is to catch it while it still looks absurd.
DEFAULT_MAX_DISAGREEMENT = 1.0

#: Largest "free money" a single quote may show against the market's own fair
#: probability before it is treated as a bad row rather than a good price.
#:
#: The disagreement breaker above guards the *model*. This one guards the
#: *price*, and it is the more dangerous of the two gaps, because EV, growth and
#: stake are all computed from ``best_american`` — the maximum across books,
#: which is by construction the input most sensitive to one stale or typo'd row.
#: The consensus that feeds ``fair_prob`` is a median and shrugs a bad row off;
#: the price we bet does not.
#:
#: The quantity gated is ``fair_prob * decimal - 1``: what this quote would
#: return at the market's *own* number, with our model taking no view at all.
#: Line shopping seven books buys a couple of points; a fair-priced book returns
#: slightly negative (that is the vig). Anything showing double-digit free money
#: at the consensus probability is a broken quote, and without this gate it
#: becomes a maximum-stake "strong" pick at 100% stated confidence — a +450 row
#: in a -110 market prices as +180% EV and clears every other rule in this file.
DEFAULT_MAX_PRICE_EDGE = 0.12

TIER_STRONG = "strong"
TIER_PLAYABLE = "playable"
TIER_THIN = "thin"
TIER_PASS = "pass"

#: Machine-readable reason a leg landed where it did. The prose in ``reasons``
#: is for the reader; this is for the board, which aggregates it into a
#: histogram so an empty page can say *why* it is empty in one line instead of
#: making someone read forty rows to work it out.
#:
#: ``no_model`` is the one that matters most. Before it existed, a game the
#: model had no view on produced an ensemble equal to the market, therefore a
#: zero edge, therefore the message "no edge at this price" — which asserts the
#: market is efficient when the truth is that we did not have an opinion. Those
#: are opposite claims and they must never share a label again.
R_NO_MODEL = "no_model"
R_BAD_QUOTE = "bad_quote"
R_MODEL_FAULT = "model_fault"
R_THIN_MARKET = "thin_market"
R_LONGSHOT = "longshot"
R_PRICED_OUT = "priced_out"
R_NO_EDGE = "no_edge"
R_EDGE_TOO_SMALL = "edge_too_small"
R_LOW_CONFIDENCE = "low_confidence"
R_TINY_STAKE = "tiny_stake"
R_OK = "ok"

REASON_LABEL: dict[str, str] = {
    R_NO_MODEL: "no model view for this game",
    R_BAD_QUOTE: "quote looks stale or wrong",
    R_MODEL_FAULT: "model/market gap implies a bad input",
    R_THIN_MARKET: "too few books quoting",
    R_LONGSHOT: "priced past the longshot ceiling",
    R_PRICED_OUT: "too short to bet",
    R_NO_EDGE: "market price is fair or better",
    R_EDGE_TOO_SMALL: "edge inside our own error bar",
    R_LOW_CONFIDENCE: "edge not confidently real",
    R_TINY_STAKE: "sizes too small to place",
    R_OK: "playable",
}


#: Preset gate bundles. The board defaults to ``balanced``.
#:
#: These exist because "nothing qualifies" and "nothing was considered" look
#: identical from the outside, and the honest answer to the first is to let the
#: reader widen the net and see for themselves rather than to quietly lower the
#: bar for everyone. Note what does *not* move much across the presets: the
#: moneyline floor still excludes heavy chalk even at ``loose`` (-600 still
#: needs 86% to break even), and neither circuit breaker moves at all. Widening
#: the search is a different act from disabling the safety rails.
STRICTNESS_PRESETS: dict[str, dict[str, float]] = {
    "strict": {
        "min_ev": 0.03, "min_p_edge_real": 0.62, "ml_price_floor": -200,
        "max_price": 500, "min_books": 4, "min_stake_units": 0.20,
    },
    "balanced": {
        "min_ev": 0.015, "min_p_edge_real": 0.55, "ml_price_floor": -350,
        "max_price": 800, "min_books": 2, "min_stake_units": 0.10,
    },
    "loose": {
        "min_ev": 0.005, "min_p_edge_real": 0.51, "ml_price_floor": -600,
        "max_price": 1200, "min_books": 1, "min_stake_units": 0.02,
    },
}

#: Tier ordering for sorting/formatting.
TIER_RANK = {TIER_STRONG: 0, TIER_PLAYABLE: 1, TIER_THIN: 2, TIER_PASS: 3}

MARKET_LABEL = {
    MARKET_MONEYLINE: "Moneyline",
    MARKET_SPREAD: "Spread",
    MARKET_TOTAL: "Total",
}


@dataclass(frozen=True)
class BoardRules:
    """Every threshold the board applies, in one object it can report back.

    Carried into the payload verbatim so the UI can state *why* a slate produced
    three picks instead of thirty. A betting board that filters silently is
    indistinguishable from a broken one.
    """

    min_ev: float = DEFAULT_MIN_EV
    min_p_edge_real: float = DEFAULT_MIN_P_EDGE_REAL
    ml_price_floor: int = DEFAULT_ML_PRICE_FLOOR
    max_price: int = DEFAULT_MAX_PRICE
    min_books: int = DEFAULT_MIN_BOOKS
    min_stake_units: float = DEFAULT_MIN_STAKE_UNITS
    kelly_fraction: float = DEFAULT_KELLY_FRACTION
    kelly_cap: float = DEFAULT_KELLY_CAP
    tail_tau_inflation: float = DEFAULT_TAIL_TAU_INFLATION
    max_disagreement: float = DEFAULT_MAX_DISAGREEMENT
    max_price_edge: float = DEFAULT_MAX_PRICE_EDGE

    def as_dict(self) -> dict:
        return {
            "min_ev": self.min_ev,
            "min_p_edge_real": self.min_p_edge_real,
            "ml_price_floor": self.ml_price_floor,
            "max_price": self.max_price,
            "min_books": self.min_books,
            "min_stake_units": self.min_stake_units,
            "kelly_fraction": self.kelly_fraction,
            "kelly_cap": self.kelly_cap,
            "tail_tau_inflation": self.tail_tau_inflation,
            "max_disagreement": self.max_disagreement,
            "max_price_edge": self.max_price_edge,
        }


# --------------------------------------------------------------------------- #
# The scored pick
# --------------------------------------------------------------------------- #


@dataclass
class ValuePick:
    """One leg, scored as a standalone bet."""

    leg: LegCandidate

    # Pricing
    breakeven_prob: float        # unconditional win prob needed to break even
    breakeven_vs_ours: float     # the same bar, conditional on no push
    fair_price_american: int     # the no-vig consensus price for this side
    model_price_american: int    # the price our probability implies
    cents_of_value: int          # offered price minus fair price, in American cents
    hold: float                  # the book's overround on this market

    # Quality
    ev: float                    # per unit staked (push-aware)
    roi_pct: float               # ev * 100, the number bettors actually think in
    p_edge_real: float           # P(true prob > breakeven) given residual noise
    tau_effective: float         # logit-scale sd used for the above
    growth_rate: float           # expected log-bankroll growth at optimal stake

    # Sizing (1 unit == 1% of bankroll)
    stake_fraction: float
    stake_units: float

    # Verdict
    tier: str
    band: str
    reason_code: str = R_OK          # machine-readable; drives the board histogram
    model_available: bool = True     # False => market-only, we have no opinion here
    reasons: list[str] = field(default_factory=list)     # why it is (not) playable
    is_recommended: bool = False

    # ------------------------------------------------------------------ #

    @property
    def sort_key(self) -> tuple:
        """Recommended first, then by expected growth. Never by raw edge.

        Two legs with the same probability edge are not the same bet: the same
        two points of edge on a +900 longshot and on a -110 spread buy wildly
        different amounts of bankroll growth. Sorting on ``edge`` — which is
        what the old board did once removed, by sorting on confidence — treats
        them as interchangeable. Growth rate does not.

        Note the tier comes first. Gating decides *whether* to bet; growth only
        orders what survived. See :func:`growth_rate` for why growth must not be
        asked to do the gating as well.
        """
        return (TIER_RANK.get(self.tier, 9), -self.growth_rate, -self.ev)

    def as_dict(self) -> dict:
        leg = self.leg
        return {
            # --- identity ---
            "key": leg.key,
            "event_id": leg.event_id,
            "market": leg.market,
            "market_label": MARKET_LABEL.get(leg.market, leg.market),
            "side": leg.side,
            "label": leg.label,
            "team_id": leg.team_id,
            "opponent_id": leg.opponent_id,
            "line": leg.line,
            # --- price ---
            "price_american": leg.price_american,
            "decimal_odds": round(leg.decimal_odds, 4),
            "book": leg.book,
            "n_books": leg.n_books,
            "is_alt": leg.is_alt,
            "fair_source": leg.fair_source,
            "notes": list(leg.notes),
            "fair_price_american": self.fair_price_american,
            "model_price_american": self.model_price_american,
            "cents_of_value": self.cents_of_value,
            "hold": round(self.hold, 4),
            # --- probability ---
            "fair_prob": round(leg.fair_prob, 4),
            "model_prob": round(leg.model_prob, 4),
            "prob": round(leg.prob, 4),
            "push_prob": round(leg.push_prob, 4),
            "breakeven_prob": round(self.breakeven_prob, 4),
            "breakeven_vs_ours": round(self.breakeven_vs_ours, 4),
            "edge": round(leg.edge, 4),
            # --- quality ---
            "expected_value": round(self.ev, 4),
            "roi_pct": round(self.roi_pct, 2),
            "p_edge_real": round(self.p_edge_real, 4),
            "tau_effective": round(self.tau_effective, 4),
            "growth_rate": round(self.growth_rate, 6),
            # --- sizing ---
            "stake_fraction": round(self.stake_fraction, 5),
            "stake_units": round(self.stake_units, 2),
            # --- verdict ---
            "tier": self.tier,
            "band": self.band,
            "reason_code": self.reason_code,
            "reason_label": REASON_LABEL.get(self.reason_code, self.reason_code),
            "model_available": self.model_available,
            "is_recommended": self.is_recommended,
            "reasons": list(self.reasons),
            "is_favorite": leg.is_favorite,
        }


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #


def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def tail_inflated_tau(tau: float, fair_prob: float, inflation: float) -> float:
    """Residual uncertainty, widened toward the ends of the price curve.

    ``tau`` from :func:`shrinkage.leg_tau` is a single logit-scale number fitted
    across the whole board. Applied unchanged at ``p = 0.96`` it asserts that we
    know a heavy favourite's probability as precisely as we know a pick'em's,
    which is the assumption that let a 1-point chalk edge read as a lock.

    The multiplier is ``1 + inflation * (2|p - 0.5|)^2``: exactly 1 at a
    pick'em, and growing quadratically so the widening is negligible through the
    middle of the board and material only where the model is genuinely out of
    sample. Quadratic rather than linear because the unmodelled effects out
    there (rest-starters, garbage time, backdoor covers) do not scale with the
    price -- they switch on once a game is decided.
    """
    d = 2.0 * abs(clamp(fair_prob, 1e-6, 1 - 1e-6) - 0.5)
    return tau * (1.0 + max(0.0, inflation) * d * d)


def breakeven_probability(decimal_odds: float, push_prob: float) -> float:
    """Win probability that makes this bet exactly break even.

    A push refunds the stake, so it neither helps nor hurts EV per unit -- but
    it does consume probability mass that can no longer win. Setting
    ``EV = p*dec + push - 1 = 0`` gives ``p = (1 - push) / dec``.
    """
    if decimal_odds <= 1.0:
        return 1.0
    return clamp((1.0 - clamp(push_prob, 0.0, 1.0)) / decimal_odds, 0.0, 1.0)


def return_moments(
    win_prob: float, push_prob: float, decimal_odds: float,
) -> tuple[float, float]:
    """``(E[R], sd[R])`` for a single bet whose gross return R is not binary.

    R is ``decimal_odds`` on a win, ``1`` on a push, ``0`` on a loss. Both
    moments are needed because sizing and growth ranking both run off
    ``E[(R-1)^2]``, and a push materially changes it.
    """
    p_w = clamp(win_prob, 0.0, 1.0)
    p_p = clamp(push_prob, 0.0, 1.0)
    p_l = max(0.0, 1.0 - p_w - p_p)
    mean = p_w * decimal_odds + p_p * 1.0
    second = p_w * decimal_odds ** 2 + p_p * 1.0
    var = max(0.0, second - mean * mean)
    return mean, math.sqrt(var)


def growth_rate(expected_return: float, return_sd: float) -> float:
    """Expected log-bankroll growth per bet at the optimal (Kelly) stake.

    ``g = (E[R] - 1)^2 / (2 * E[(R - 1)^2])`` — the second-order expansion of
    ``max_f E[log(1 - f + fR)]``. This is the ranking quantity for the board.

    Why growth rather than EV: EV per unit staked treats a 4% edge on a +900
    longshot and a 4% edge on a -110 spread as the same bet. They are not. The
    longshot's edge is collected through enormous variance, so at any sane stake
    it compounds a bankroll far more slowly, and a run of losses that means
    nothing statistically can still end the experiment. Dividing the edge by
    what it costs in variance to collect is the only ranking under which "worth
    betting" has a defined meaning.

    What growth does **not** do — and this is worth being explicit about,
    because it is tempting to assume otherwise — is penalise heavy chalk. If a
    -500 favourite's 4% edge were real, it would grow a bankroll *faster* than a
    +150 dog's 10% edge, because it is collected at a fraction of the variance.
    That is correct finance and this function reports it correctly.

    Chalk is demoted somewhere else entirely, and deliberately so: by the trust
    layer (band-aware shrinkage plus tail-inflated ``tau``, which say we do not
    believe the edge is there) and by the moneyline price floor (which says we
    would not take it even if it were). Ranking and gating are separate jobs.
    Conflating them is what produced the original confidence-sorted board.
    """
    excess = expected_return - 1.0
    if excess <= 0:
        return 0.0
    denom = return_sd * return_sd + excess * excess
    return 0.0 if denom <= 0 else (excess * excess) / (2.0 * denom)


def score_leg(
    leg: LegCandidate,
    *,
    tau: float,
    band: str,
    rules: BoardRules | None = None,
    hold: float = 0.0,
    model_available: bool = True,
) -> ValuePick:
    """Score one priceable leg as a standalone bet.

    ``leg.prob`` is already calibrated and shrunk toward the market by
    :mod:`legs`; nothing here re-derives it. This function only asks what that
    probability is worth at the offered price, how confident we are in the sign
    of the answer, and how much to stake.
    """
    r = rules or BoardRules()
    dec = leg.decimal_odds
    win_p = clamp(leg.prob, 0.0, 1.0) * max(0.0, 1.0 - leg.push_prob)

    breakeven = breakeven_probability(dec, leg.push_prob)
    # The bar stated on the *same* conditioning basis as `leg.prob`. Both
    # `leg.prob` and `leg.fair_prob` are conditional on no push (legs.py divides
    # the model triple by 1 - push, and the book's two prices only pay when
    # there is no push), so this is the only version of break-even that can be
    # honestly compared to them. `breakeven` above is unconditional and is what
    # EV is defined against; showing that one next to `leg.prob` overstates the
    # edge by push x breakeven on every whole-number line.
    breakeven_cond = clamp(
        breakeven / max(1e-9, 1.0 - leg.push_prob) if leg.push_prob < 1.0 else 1.0,
        1e-4, 1 - 1e-4,
    )
    ev = win_p * dec + leg.push_prob - 1.0

    # What this quote returns at the market's own fair number, with our model
    # taking no view. Line shopping earns a point or two; double digits is a
    # broken row. Guarded in _classify.
    price_edge = leg.fair_prob * dec - 1.0

    mean_r, sd_r = return_moments(win_p, leg.push_prob, dec)
    g = growth_rate(mean_r, sd_r)

    stake_fraction = odds_math.kelly_from_moments(
        mean_r, sd_r, cap=r.kelly_cap, fraction=r.kelly_fraction,
    )
    # 1 unit == 1% of bankroll, the convention the bet tracker already uses.
    stake_units = stake_fraction * 100.0

    tau_eff = tail_inflated_tau(tau, leg.fair_prob, r.tail_tau_inflation)
    # P(the true probability clears the break-even bar), on the log-odds scale
    # where the shrunk residual is approximately normal. Both sides are
    # conditional on no push: `leg.prob` is conditional by construction, and
    # `breakeven_cond` divides the push mass back out. Comparing a conditional
    # probability to an unconditional bar would report edge that is not there.
    ours = clamp(leg.prob, 1e-4, 1 - 1e-4)
    z = (logit(ours) - logit(breakeven_cond)) / max(1e-6, tau_eff)
    p_edge_real = _norm_cdf(z)

    fair_price = _safe_price(leg.fair_prob)
    model_price = _safe_price(leg.prob)
    cents = _cents_of_value(leg.price_american, fair_price)

    tier, reason_code, reasons = _classify(
        leg, ev=ev, p_edge_real=p_edge_real, stake_units=stake_units,
        price_edge=price_edge, rules=r, model_available=model_available,
    )
    if tier == TIER_PASS:
        # Kelly on a bet we are not making is a number that can only mislead --
        # a capped 3u next to "no bet" reads as a contradiction. The sizing math
        # still ran (it feeds the growth ranking); it is simply not reported.
        stake_fraction = 0.0
        stake_units = 0.0

    return ValuePick(
        leg=leg,
        breakeven_prob=breakeven,
        breakeven_vs_ours=breakeven_cond,
        fair_price_american=fair_price,
        model_price_american=model_price,
        cents_of_value=cents,
        hold=hold,
        ev=ev,
        roi_pct=ev * 100.0,
        p_edge_real=p_edge_real,
        tau_effective=tau_eff,
        growth_rate=g,
        stake_fraction=stake_fraction,
        stake_units=stake_units,
        tier=tier,
        band=band,
        reason_code=reason_code,
        model_available=model_available,
        reasons=reasons,
        is_recommended=tier in (TIER_STRONG, TIER_PLAYABLE),
    )


def _safe_price(prob: float) -> int:
    try:
        return odds_math.implied_to_american(clamp(prob, 1e-4, 1 - 1e-4))
    except ValueError:  # pragma: no cover — clamp makes this unreachable
        return 0


def _cents_ladder(price: int) -> float:
    """Map an American price onto the continuous "cents" ladder bettors use.

    American odds are discontinuous at zero -- there is no -50 and no +50, and
    -100 and +100 are the same price -- so subtracting two of them directly is
    meaningless the moment they straddle the boundary. The ladder folds the two
    branches into one monotone scale anchored at pick'em:

        +160 ->  +60      -110 ->  -10      -400 -> -300

    A difference on this scale is exactly what a trader means by "we are getting
    ten cents of value": -110 versus +100 is ten cents, +160 versus +140 is
    twenty, and -400 versus -300 is a hundred.
    """
    if price >= 100:
        return float(price - 100)
    if price <= -100:
        return float(price + 100)
    # Prices inside (-100, 100) are not quotable; clamp to pick'em.
    return 0.0


def _cents_of_value(offered: int, fair: int) -> int:
    """How much better than fair the offered price is, in American cents."""
    return int(round(_cents_ladder(offered) - _cents_ladder(fair)))


def _classify(
    leg: LegCandidate,
    *,
    ev: float,
    p_edge_real: float,
    stake_units: float,
    price_edge: float,
    rules: BoardRules,
    model_available: bool = True,
) -> tuple[str, str, list[str]]:
    """Tier the bet, and say plainly why it landed there.

    Returns ``(tier, reason_code, reasons)``. Every rejection carries both a
    machine-readable code (for the board's histogram) and a sentence. "No bet"
    without a reason is the failure mode this rewrite exists to fix, just
    pointed the other way.

    Order matters. The two circuit breakers run first because a faulted leg
    clears every ordinary rule with room to spare. The model-availability check
    runs before the edge checks because a leg with no model behind it has an
    edge of zero *by construction*, and reporting that as "the market price is
    fair" is a false statement about the market.
    """
    reasons: list[str] = []

    # 0. Do we even have an opinion? When the prediction store is cold, the
    #    ensemble collapses onto the market, the edge is identically zero, and
    #    every downstream check would truthfully report "no edge" while meaning
    #    something completely different. Say the real thing instead.
    if not model_available:
        reasons.append(
            "no model view for this game — the prediction store has no "
            "distribution for it, so this row is the market's price with nothing "
            "of ours to compare against. Not an efficient market; an absent opinion"
        )
        return TIER_PASS, R_NO_MODEL, reasons

    # 1. Circuit breaker on the model.
    gap = abs(logit(clamp(leg.model_prob, 1e-4, 1 - 1e-4))
              - logit(clamp(leg.fair_prob, 1e-4, 1 - 1e-4)))
    if gap > rules.max_disagreement:
        reasons.append(
            f"model disagrees with the market by {gap:.2f} log-odds — larger than "
            "any real edge, so this is treated as a bad input (stale line, "
            "mismatched number, or a team the model cannot rate) rather than value"
        )
        return TIER_PASS, R_MODEL_FAULT, reasons

    # 2. Circuit breaker on the price. EV and stake are computed from the best
    #    quote across books, so one stale row is enough to manufacture a
    #    maximum-stake recommendation.
    if price_edge > rules.max_price_edge:
        reasons.append(
            f"this quote pays {price_edge * 100:+.0f}% over the market's own fair "
            "number — no book is that far off a multi-book consensus, so the price "
            "is treated as a stale or bad quote rather than value"
        )
        return TIER_PASS, R_BAD_QUOTE, reasons

    # 3. Hard gates.
    if leg.n_books < rules.min_books:
        reasons.append(
            f"only {leg.n_books} book{'s' if leg.n_books != 1 else ''} quoting — "
            "a one-book consensus is that book's hold, not a market"
        )
        return TIER_PASS, R_THIN_MARKET, reasons

    if leg.price_american > rules.max_price:
        reasons.append(
            f"longer than {rules.max_price:+d} — out here the de-vig is mostly "
            "the book's hold distribution, so the fair number is unreliable"
        )
        return TIER_PASS, R_LONGSHOT, reasons

    if leg.market == MARKET_MONEYLINE and leg.price_american < rules.ml_price_floor:
        need = 100.0 * abs(leg.price_american) / (abs(leg.price_american) + 100.0)
        reasons.append(
            f"priced out — {leg.price_american} needs {need:.0f}% just to break even; "
            "the risk is many times the win and the model has least resolution here"
        )
        return TIER_PASS, R_PRICED_OUT, reasons

    if ev <= 0:
        reasons.append(
            f"no edge at this price — {ev * 100:+.1f}% EV once our edge is shrunk "
            "to what settled history supports"
        )
        return TIER_PASS, R_NO_EDGE, reasons

    # 4. Soft gates. Everything below here is +EV and shown on the board; the
    #    tier says how much weight to put on it.
    if ev < rules.min_ev:
        reasons.append(
            f"{ev * 100:+.1f}% EV is inside the error bar on our own edge estimate "
            f"(needs {rules.min_ev * 100:.1f}%)"
        )
        return TIER_THIN, R_EDGE_TOO_SMALL, reasons

    if p_edge_real < rules.min_p_edge_real:
        reasons.append(
            f"only {p_edge_real * 100:.0f}% confident the edge is real rather than "
            f"estimation noise (needs {rules.min_p_edge_real * 100:.0f}%)"
        )
        return TIER_THIN, R_LOW_CONFIDENCE, reasons

    if stake_units < rules.min_stake_units:
        reasons.append(
            f"sizes to {stake_units:.2f}u — too small to be worth the ticket"
        )
        return TIER_THIN, R_TINY_STAKE, reasons

    if ev >= 2.0 * rules.min_ev and p_edge_real >= 0.68:
        reasons.append(
            f"{ev * 100:+.1f}% EV at {p_edge_real * 100:.0f}% confidence the edge is real"
        )
        return TIER_STRONG, R_OK, reasons

    reasons.append(
        f"{ev * 100:+.1f}% EV, {p_edge_real * 100:.0f}% confident it is real"
    )
    return TIER_PLAYABLE, R_OK, reasons


# --------------------------------------------------------------------------- #
# Explanation
# --------------------------------------------------------------------------- #


def explain(pick: ValuePick) -> str:
    """One sentence of the arithmetic — analysis, not a call to action.

    Legal constraint: this string is rendered on the Value Board card. It must
    not tell the reader to bet, size a stake, or label the row a play / no-bet.
    The numbers (price, fair, model, EV, break-even vs ours) are the product.
    """
    leg = pick.leg
    price = f"{leg.price_american:+d}"
    at_book = f" at {leg.book}" if leg.book else ""
    fair = f"{pick.fair_price_american:+d}"

    if pick.reason_code == R_NO_MODEL:
        return (
            f"{leg.label} {price}{at_book} — market price only. Sparky has no model "
            "distribution for this game, so it is not saying the price is fair; it "
            "is saying it has nothing to compare it to."
        )

    if pick.tier == TIER_PASS:
        head = f"{leg.label} {price}{at_book}."
        return f"{head} {pick.reasons[0].capitalize()}." if pick.reasons else head

    body = (
        f"{leg.label} {price}{at_book}. "
        f"Market fair value is {fair}; model makes it {pick.model_price_american:+d}, "
        f"{pick.ev * 100:+.1f}% EV — needs {pick.breakeven_vs_ours * 100:.1f}% "
        f"to break even, model has {leg.prob * 100:.1f}%."
    )
    if pick.tier == TIER_THIN and pick.reasons:
        body += f" {pick.reasons[-1].rstrip('.').capitalize()}."
    return body


def market_summary(picks: list[ValuePick]) -> dict:
    """Counts and best-in-class per market, for the board header."""
    out: dict[str, dict] = {}
    for m in (MARKET_MONEYLINE, MARKET_SPREAD, MARKET_TOTAL):
        subset = [p for p in picks if p.leg.market == m]
        rec = [p for p in subset if p.is_recommended]
        out[m] = {
            "label": MARKET_LABEL[m],
            "priced": len(subset),
            "recommended": len(rec),
            "best_ev": round(max((p.ev for p in rec), default=0.0), 4),
            "total_stake_units": round(sum(p.stake_units for p in rec), 2),
        }
    return out
