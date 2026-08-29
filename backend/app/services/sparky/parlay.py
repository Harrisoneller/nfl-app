"""Parlay search and ranking.

What changed and why
--------------------
The previous engine took N games the user had already chosen, enumerated the
``2**N`` ways to pick a winner, and ranked them by

    composite = confidence x signal_alignment x underdog_balance x value_factor

That ranking has three structural problems, independent of how good the model
underneath it is:

- It is **not monotone in expected value**. Because the four factors multiply
  and only one of them looks at edge, a high-confidence, well-balanced, -EV
  ticket routinely outranks a modest +EV one. A betting product whose top
  recommendation can be -EV is not a betting product.
- ``underdog_balance`` hard-coded "about a third underdogs is ideal" from the
  original spec. Nothing in this codebase ever measured that. It was a prior
  wearing the costume of a result.
- The **search space was the user's**, not the model's. The slate's best
  available ticket was never looked for; the daily "recommended parlay" was
  literally the three highest-*confidence* games, which is close to the
  opposite of the three best *bets* — confidence is highest exactly where the
  market already agrees and there is no edge left.

This module replaces all of that with two quantities and nothing else:

    ev_adjusted = E[return] - 1 - selection_penalty          (the gate)
    growth_rate = ev_adjusted^2 / (2 * E[(R - 1)^2])         (the ranking)

``E[return]`` comes from :mod:`correlation` (push-aware, correlation-aware) and
``selection_penalty`` from :mod:`shrinkage` (the residual winner's curse).
Tickets must clear the gate, and among those that do they are ordered by
expected log-bankroll growth rather than by expected value — see
:func:`growth_rate` for why that distinction decides whether the page shows
sensible tickets or lottery tickets. Everything else — leg count, favourite/dog
mix, market type — is an *output* of that optimization, not an input constraint
dressed up as wisdom.

Search strategy
---------------
Pricing every ticket exactly is not affordable: a 40-game slate yields a few
hundred candidate legs and millions of combinations. Three stages instead:

1. **Screen** every combination with the closed-form independent product,
   which costs a handful of multiplications and is monotone enough to rank
   candidates for the next stage.
2. **Price** the top few hundred with the fast rank-1 correlated quadrature.
3. **Re-price** the shortlist with the exact per-channel integration, apply the
   selection penalty, and apply the +EV gate.

Stage 3 exists because the rank-1 collapse can be off by up to about 1% of
expected return, which is small in absolute terms but is a meaningful fraction
of a real edge — quite capable of moving a borderline ticket across the gate.
The finalists are the only tickets a user will ever see, so they get the exact
number.

The strict gate
---------------
By default only tickets with ``ev_adjusted > 0`` are returned. On many slates
that is **zero tickets**, and that is the correct output, not a failure. A
parlay tool that always has a recommendation is a parlay tool that is not
checking.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import combinations

from . import correlation, odds_math, shrinkage
from .correlation import ParlayPricing, PricedLeg
from .legs import LegCandidate
from .odds_math import clamp

MIN_LEGS = 2
MAX_LEGS = 8

#: How many screened candidates get the correlated quadrature.
DEFAULT_PRICE_TOP = 400
#: How many priced candidates get the exact per-channel integration.
DEFAULT_EXACT_TOP = 25
#: Hard ceiling on combinations enumerated in the screening stage.
DEFAULT_MAX_COMBOS = 400_000
#: A leg may appear in at most this many returned tickets. Without a cap the
#: output is twenty near-identical tickets built on the same two legs, which
#: reads as diversification while being one concentrated bet.
DEFAULT_MAX_LEG_REUSE = 3


@dataclass
class ParlayTicket:
    rank: int
    legs: list[LegCandidate]
    pricing: ParlayPricing
    selection_penalty: float
    ev_adjusted: float
    kelly_fraction: float
    growth_rate: float           # expected log-bankroll growth at optimal stake
    parlay_odds_american: int
    parlay_odds_decimal: float
    implied_prob: float          # vig-included, from the ticket's own price
    is_value: bool
    exact_priced: bool = False
    explanation: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def n_legs(self) -> int:
        return len(self.legs)

    # --- compatibility with the pre-rebuild RankedParlay surface ----------- #
    # The historical backtest, the demo-slate settler and the legacy persistence
    # path all read these names. They are kept as properties (not stored fields)
    # so there is exactly one source of truth and they cannot drift from the
    # pricing they describe.

    @property
    def combined_win_prob(self) -> float:
        """P(the ticket does not lose). Push-aware, unlike the old product."""
        return self.pricing.survive_prob

    @property
    def edge(self) -> float:
        return self.pricing.survive_prob - self.implied_prob

    @property
    def underdog_count(self) -> int:
        return sum(1 for leg in self.legs if not leg.is_favorite)

    @property
    def confidence_score(self) -> float:
        return round(self.pricing.survive_prob * 100.0, 1)

    @property
    def signal_alignment(self) -> float:
        """Retired. Signal alignment was a component of the old composite score.

        It is 0.0 rather than removed so legacy persistence keeps working; the
        information it carried now enters through the leg probabilities
        themselves, which is where a market signal belongs.
        """
        return 0.0

    @property
    def composite_score(self) -> float:
        """Legacy display score, now derived from growth rate rather than a
        product of four hand-weighted factors. Scaled to a comparable 0-100ish
        range so old UI and old rows still render sensibly."""
        return round(min(100.0, self.growth_rate * 1e4), 2)

    @property
    def expected_value(self) -> float:
        """Alias for the raw (pre-penalty) EV, matching the old field name."""
        return self.pricing.expected_value

    def as_dict(self) -> dict:
        return {
            "rank": self.rank,
            "n_legs": self.n_legs,
            "legs": [leg.as_dict() for leg in self.legs],
            "parlay_odds_american": self.parlay_odds_american,
            "parlay_odds_decimal": round(self.parlay_odds_decimal, 3),
            "implied_prob": round(self.implied_prob, 4),
            "hit_prob": round(self.pricing.survive_prob, 4),
            "all_win_prob": round(self.pricing.all_win_prob, 4),
            "expected_value": round(self.pricing.expected_value, 4),
            "selection_penalty": round(self.selection_penalty, 4),
            "ev_adjusted": round(self.ev_adjusted, 4),
            "kelly_fraction": round(self.kelly_fraction, 5),
            "growth_rate": round(self.growth_rate, 8),
            "is_value": self.is_value,
            "exact_priced": self.exact_priced,
            "pricing": self.pricing.as_dict(),
            "explanation": self.explanation,
            "warnings": list(self.warnings),
            # Kept so existing consumers keep rendering; both now mean something.
            "combined_win_prob": round(self.pricing.survive_prob, 4),
            "underdog_count": sum(1 for leg in self.legs if not leg.is_favorite),
        }


# --------------------------------------------------------------------------- #
# Conversion
# --------------------------------------------------------------------------- #


def to_priced(leg: LegCandidate) -> PricedLeg:
    room = max(0.0, 1.0 - leg.push_prob)
    return PricedLeg(
        key=leg.key,
        event_id=leg.event_id,
        prob=clamp(leg.prob, 0.0, 1.0) * room,
        push_prob=leg.push_prob,
        decimal_odds=leg.decimal_odds,
        tau=leg.tau,
        loadings=leg.loadings,
    )


def growth_rate(ev_adjusted: float, return_sd: float) -> float:
    """Expected log-bankroll growth per bet, at the Kelly-optimal stake.

    **This, not expected value, is what tickets are ranked by**, and the
    difference is the difference between a usable product and a lottery-ticket
    generator. Ranking by EV per dollar staked is maximized by maximum
    variance: a four-leg longshot at +12000 with a 1.3% chance can post a
    higher EV% than a two-leg at +260, while being a bet no bankroll should
    ever make. Kelly sizing already knows this — it stakes the longshot at
    0.01% and the short ticket at 2% — so the honest ranking is the one that
    reflects what each ticket contributes to *growth*, not to a hypothetical
    equal-stake return.

    Substituting the optimal ``f* = (m - 1) / E[(R - 1)^2]`` into the
    second-order expansion of ``E[log(1 - f + fR)]`` gives

        g = (m - 1)^2 / (2 * E[(R - 1)^2])

    which is one half the squared Sharpe ratio of the bet: scale-free,
    comparable across leg counts and prices, and zero for anything not +EV.
    """
    if ev_adjusted <= 0:
        return 0.0
    denom = return_sd * return_sd + ev_adjusted * ev_adjusted
    if denom <= 0:
        return 0.0
    return (ev_adjusted * ev_adjusted) / (2.0 * denom)


def _screen_growth(legs: tuple[LegCandidate, ...]) -> float:
    """Cheap growth-rate proxy from the independent, push-aware moments.

    Screening on expected value instead would quietly break the funnel: the
    top few hundred tickets by EV are overwhelmingly longshots, so the short,
    high-growth tickets the final ranking actually wants would never survive to
    stage 2 to be considered. Both moments are closed form here —
    ``E[R] = prod(p*d + q)`` and ``E[R^2] = prod(p*d^2 + q)`` — so this costs
    one extra multiply per leg.
    """
    m1 = 1.0
    m2 = 1.0
    for leg in legs:
        room = max(0.0, 1.0 - leg.push_prob)
        p = clamp(leg.prob, 0.0, 1.0) * room
        d = leg.decimal_odds
        m1 *= p * d + leg.push_prob
        m2 *= p * d * d + leg.push_prob
    ev = m1 - 1.0
    if ev <= 0:
        return ev  # keep -EV candidates ordered sensibly for strict=False callers
    var = max(0.0, m2 - m1 * m1)
    return growth_rate(ev, var ** 0.5)


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #


def _prune_pool(pool: list[LegCandidate], n_legs: int, max_combos: int) -> list[LegCandidate]:
    """Trim the pool until ``C(len(pool), n_legs)`` is affordable to enumerate.

    Trimming is by leg edge, which is the right key: a leg that is not
    individually +EV against the fair number cannot rescue a ticket, because
    the ticket's expected return is a *product* of the legs' expected returns.
    There is no such thing as a leg that is bad on its own but good in a
    parlay — that intuition comes from correlated same-game legs, which this
    engine explicitly refuses to price with the factor model.
    """
    ranked = sorted(pool, key=lambda leg: leg.edge, reverse=True)
    lo, hi = n_legs, len(ranked)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if math.comb(mid, n_legs) <= max_combos:
            lo = mid
        else:
            hi = mid - 1
    return ranked[:max(lo, n_legs)]


def search_parlays(
    pool: list[LegCandidate],
    *,
    n_legs: int,
    top_n: int = 10,
    price_top: int = DEFAULT_PRICE_TOP,
    exact_top: int = DEFAULT_EXACT_TOP,
    max_combos: int = DEFAULT_MAX_COMBOS,
    max_leg_reuse: int = DEFAULT_MAX_LEG_REUSE,
    strict: bool = True,
    scale_stakes: bool = True,
    selection_kappa: float = shrinkage.DEFAULT_SELECTION_KAPPA,
    kelly_cap: float = 0.02,
    kelly_fraction: float = 0.25,
    require_events: list[str] | None = None,
) -> list[ParlayTicket]:
    """Find the best ``n_legs`` tickets available from ``pool``.

    ``pool`` should already be shrunk and edge-filtered by
    :func:`legs.build_leg_pool`. One leg per game is enforced. With
    ``strict=True`` (the default) only tickets that survive the selection
    penalty with positive expected value are returned — often none.
    """
    if not MIN_LEGS <= n_legs <= MAX_LEGS:
        raise ValueError(f"A parlay needs {MIN_LEGS}..{MAX_LEGS} legs (got {n_legs})")

    by_event: dict[str, list[LegCandidate]] = {}
    for leg in pool:
        by_event.setdefault(leg.event_id, []).append(leg)
    if len(by_event) < n_legs:
        return []

    pool_size = len(pool)
    working = _prune_pool(pool, n_legs, max_combos)

    # --- stage 1: screen ---------------------------------------------------- #
    required = set(require_events or [])
    screened: list[tuple[float, tuple[LegCandidate, ...]]] = []
    for combo in combinations(working, n_legs):
        events = {leg.event_id for leg in combo}
        if len(events) != n_legs:
            continue                       # one leg per game
        if required and not required.issubset(events):
            continue
        screened.append((_screen_growth(combo), combo))
    if not screened:
        return []
    screened.sort(key=lambda t: t[0], reverse=True)

    # --- stage 2: correlated pricing on the shortlist ----------------------- #
    priced: list[tuple[float, tuple[LegCandidate, ...], ParlayPricing]] = []
    for _, combo in screened[:max(price_top, top_n)]:
        pr = correlation.price_parlay([to_priced(leg) for leg in combo])
        priced.append((growth_rate(pr.expected_value, pr.return_sd), combo, pr))
    priced.sort(key=lambda t: t[0], reverse=True)

    # --- stage 3: exact re-pricing, penalty, gate --------------------------- #
    #
    # Two passes, and the split is the whole point.
    #
    # The selection penalty is an order statistic: it charges the ticket for
    # being the best of N looks, so the ticket presented *first* must carry the
    # *largest* charge. An earlier version computed it inside this loop with
    # `rank=idx`, where idx was the position in the **screening** order, and then
    # re-sorted the survivors by growth at the end. Those are different
    # orderings, so the ticket that surfaced at rank 1 was systematically the one
    # that had been charged least — measured at 0.00002 against the 0.068 its
    # legs actually warranted. That inverts the correction: the final ranking
    # ends up selecting for under-penalised tickets, which is precisely the
    # winner's curse the penalty exists to remove.
    #
    # So: price everything first, establish the selection ordering on the
    # *unpenalised* number (that ordering is what "being selected" means), then
    # charge by position in it.

    shortlist: list[tuple[tuple[LegCandidate, ...], ParlayPricing, ParlayPricing, bool]] = []
    for idx, (_, combo, fast_pr) in enumerate(priced[:max(exact_top, top_n)], start=1):
        # Only the shortlist gets the exact integrator — it is ~60x slower, and
        # a ticket ranked 40th is never going to be acted on. The rest keep the
        # rank-1 price and say so via `exact_priced`.
        use_exact = idx <= exact_top
        pr = (
            correlation.price_parlay([to_priced(leg) for leg in combo], exact=True)
            if use_exact else fast_pr
        )
        shortlist.append((combo, pr, fast_pr, use_exact))

    # The selection ordering: how attractive each ticket looked *before* the
    # correction. Ties broken by EV for the same reason as the final sort.
    shortlist.sort(
        key=lambda t: (growth_rate(t[1].expected_value, t[1].return_sd),
                       t[1].expected_value),
        reverse=True,
    )

    finalists: list[ParlayTicket] = []
    leg_uses: dict[str, int] = {}
    for position, (combo, pr, fast_pr, use_exact) in enumerate(shortlist, start=1):
        penalty = shrinkage.selection_penalty(
            [clamp(leg.prob, 0.0, 1.0) * max(0.0, 1.0 - leg.push_prob) for leg in combo],
            [leg.decimal_odds for leg in combo],
            [leg.tau for leg in combo],
            [leg.push_prob for leg in combo],
            n_leg_pool=pool_size,
            rank=position,
            kappa=selection_kappa,
        )
        ev_adj = pr.expected_value - penalty
        if strict and ev_adj <= 0:
            continue
        if any(leg_uses.get(leg.key, 0) >= max_leg_reuse for leg in combo):
            continue

        dec = pr.decimal_odds
        kelly = odds_math.kelly_from_moments(
            1.0 + ev_adj, pr.return_sd, cap=kelly_cap, fraction=kelly_fraction,
        )
        ticket = ParlayTicket(
            rank=0,
            legs=list(combo),
            pricing=pr,
            selection_penalty=penalty,
            ev_adjusted=ev_adj,
            kelly_fraction=kelly,
            growth_rate=growth_rate(ev_adj, pr.return_sd),
            parlay_odds_american=odds_math.decimal_to_american(dec),
            parlay_odds_decimal=dec,
            implied_prob=odds_math.decimal_to_implied(dec),
            is_value=ev_adj > 0,
            exact_priced=use_exact,
            warnings=_warnings(combo, pr, fast_pr),
        )
        finalists.append(ticket)
        for leg in combo:
            leg_uses[leg.key] = leg_uses.get(leg.key, 0) + 1
        if len(finalists) >= top_n:
            break

    # Deliberately NOT re-sorted here.
    #
    # `finalists` is already in selection order — sorted above on the
    # *unpenalised* price, which is what "this ticket was selected" means — and
    # each has been charged the order statistic for its position in that order.
    # Re-sorting on `ev_adjusted` undoes it: since `ev_adjusted = raw_ev -
    # penalty` and the penalty falls with rank, the sort puts the least-penalised
    # ticket back on top, which is the exact inversion the two-pass structure
    # above exists to remove. You cannot both charge by rank and rank by the
    # charge; it is circular.
    #
    # Visible consequence, which is a true statement and not a display bug:
    # rank 1 can show a lower adjusted EV than rank 2. It means the two look
    # similar on raw numbers and rank 1 is discounted harder for having been the
    # best of N looks.
    #
    # The all-negative case stays fixed, because the selection sort key is
    # `(growth_rate(raw), raw_ev)` — growth is identically zero for every -EV
    # ticket, so raw EV breaks the tie and the least-bad ticket leads. That was
    # the earlier "worst ticket at rank 1" bug; it is now pinned on raw EV.
    for i, t in enumerate(finalists, start=1):
        t.rank = i
        t.explanation = explain(t)
    return _apply_portfolio_scaling(finalists) if scale_stakes else finalists


def best_parlays(
    pool: list[LegCandidate],
    *,
    leg_counts: tuple[int, ...] = (2, 3, 4),
    top_n: int = 8,
    **kwargs,
) -> list[ParlayTicket]:
    """Search several leg counts and return the best tickets across all of them.

    The optimal leg count is an *output*. Adding a leg multiplies in another
    edge but also another vig and another chance to be wrong; which side wins
    depends on the slate, and there is no reason to decide it in advance. The
    old engine's fixed three-leg default was inherited from the original spec,
    not from anything observed.
    """
    kwargs.pop("scale_stakes", None)
    out: list[ParlayTicket] = []
    for n in leg_counts:
        if n > len({leg.event_id for leg in pool}):
            continue
        # Stakes are scaled once, at the end, over the combined result — doing it
        # per leg-count would compound the haircut and double the warning text.
        out.extend(search_parlays(
            pool, n_legs=n, top_n=top_n, scale_stakes=False, **kwargs
        ))
    # Same tie-break as search_parlays, and for the same reason: across leg
    # counts every -EV ticket has growth 0, so without EV in the key the merged
    # list is ordered arbitrarily and the worst ticket can surface first.
    # Same ordering discipline as search_parlays: merge on the *unpenalised*
    # price so each ticket's charged order statistic still matches where it
    # lands. Sorting the merged list on `ev_adjusted` reintroduces the
    # circularity described there, across leg counts instead of within one.
    out.sort(
        key=lambda t: (
            growth_rate(t.pricing.expected_value, t.pricing.return_sd),
            t.pricing.expected_value,
        ),
        reverse=True,
    )
    trimmed = out[:top_n]
    for i, t in enumerate(trimmed, start=1):
        t.rank = i
        t.explanation = explain(t)
    return _apply_portfolio_scaling(trimmed)


# --------------------------------------------------------------------------- #
# Portfolio sizing
# --------------------------------------------------------------------------- #


def _apply_portfolio_scaling(tickets: list[ParlayTicket]) -> list[ParlayTicket]:
    """Scale down stakes when recommended tickets share legs.

    Kelly sizing is derived one bet at a time. Recommending five tickets that
    all contain the same anchor leg and staking each at its own Kelly fraction
    is not five bets — it is one leveraged bet on that anchor, at five times the
    intended size. We divide each ticket's stake by the square root of how many
    recommended tickets its legs appear in, which is the standard adjustment for
    correlated simultaneous positions and degrades gracefully as overlap falls.
    """
    if not tickets:
        return tickets
    uses: dict[str, int] = {}
    for t in tickets:
        for leg in t.legs:
            uses[leg.key] = uses.get(leg.key, 0) + 1
    for t in tickets:
        overlap = max(uses.get(leg.key, 1) for leg in t.legs)
        if overlap > 1:
            t.kelly_fraction = t.kelly_fraction / math.sqrt(overlap)
            t.warnings.append(
                f"stake scaled down: shares a leg with {overlap - 1} other "
                f"recommended ticket{'s' if overlap > 2 else ''}"
            )
    return tickets


# --------------------------------------------------------------------------- #
# Explanations and warnings
# --------------------------------------------------------------------------- #


def _warnings(
    combo: tuple[LegCandidate, ...], exact: ParlayPricing, fast: ParlayPricing,
) -> list[str]:
    out: list[str] = []
    if exact.rank1_residual > 0.05:
        out.append(
            f"legs have unusual correlation structure (rank-1 residual "
            f"{exact.rank1_residual:.3f}); priced exactly"
        )
    if abs(exact.expected_value - fast.expected_value) > 0.01:
        out.append(
            f"fast and exact pricing differ by "
            f"{abs(exact.expected_value - fast.expected_value):.3f} per unit"
        )
    thin = [leg.label for leg in combo if leg.n_books < 4]
    if thin:
        out.append(f"thin market ({', '.join(thin)}): fair price is less certain")
    longshots = [leg.label for leg in combo if leg.price_american >= 400]
    if longshots:
        out.append(f"longshot leg(s) ({', '.join(longshots)}): de-vig is least reliable here")
    if exact.mean_pairwise_corr < -0.05:
        out.append("legs partly offset each other; the ticket is worth less than the product")
    return out


def explain(t: ParlayTicket) -> str:
    """Plain-English account of where the number came from, including the deductions."""
    picks = ", ".join(f"{leg.label} ({leg.price_american:+d})" for leg in t.legs)
    p = t.pricing

    lead = (
        f"{picks} at {t.parlay_odds_american:+d}. "
        f"Model gives it {p.survive_prob * 100:.1f}% to cash against the "
        f"{t.implied_prob * 100:.1f}% the price implies."
    )

    parts: list[str] = []
    if abs(p.correlation_effect) >= 0.002:
        direction = "worth more than" if p.correlation_effect > 0 else "worth less than"
        parts.append(
            f"legs are correlated (avg {p.mean_pairwise_corr:+.2f}), which makes it "
            f"{direction} the naive multiplication by "
            f"{abs(p.correlation_effect) * 100:.1f} cents on the dollar"
        )
    if p.push_effect >= 0.002:
        parts.append(
            f"push protection on the whole numbers is worth "
            f"{p.push_effect * 100:.1f} cents"
        )
    if t.selection_penalty >= 0.001:
        parts.append(
            f"less {t.selection_penalty * 100:.1f} cents for having picked these legs "
            f"out of the slate rather than been handed them"
        )
    detail = ("; ".join(parts) + ". ") if parts else ""

    verdict = (
        f"Net {t.ev_adjusted * 100:+.1f}% expected on the dollar."
        if t.is_value else
        f"Net {t.ev_adjusted * 100:+.1f}% — not a bet."
    )
    stake = (
        f" Suggested stake {t.kelly_fraction * 100:.2f}% of bankroll."
        if t.kelly_fraction > 0 else ""
    )
    return f"{lead} {detail}{verdict}{stake}"


# --------------------------------------------------------------------------- #
# Deprecated compatibility shim
# --------------------------------------------------------------------------- #


@dataclass
class GameForParlay:
    """Legacy moneyline-only input. Deprecated; use :mod:`legs` instead.

    Retained so the historical backtest and any external caller keep importing
    cleanly. It cannot express spreads, totals, pushes, best-price shopping or
    de-vigged fair probabilities, which is most of what makes the new engine
    work — anything built on it is priced on moneylines alone.
    """

    event_id: str
    home_id: str | None
    away_id: str | None
    home_ml: int | None
    away_ml: int | None
    home_prob: float
    favorite: str
    signals: list = field(default_factory=list)
    label: str = ""


def legs_from_legacy(
    games: list[GameForParlay],
    *,
    shrink: shrinkage.EdgeShrink | None = None,
    cal: shrinkage.PlattMap | None = None,
    tau: float | None = None,
) -> list[LegCandidate]:
    """Adapt legacy ``GameForParlay`` rows into moneyline legs."""
    from .legs import GameLegInputs, SidePrices, build_game_legs

    tau_v = tau if tau is not None else shrinkage.leg_tau(shrink)
    out: list[LegCandidate] = []
    for g in games:
        if g.home_ml is None or g.away_ml is None:
            continue  # no price, no leg
        out.extend(build_game_legs(
            GameLegInputs(
                event_id=g.event_id, home_id=g.home_id, away_id=g.away_id,
                label=g.label,
                ml_prices=SidePrices(
                    consensus_a=g.home_ml, consensus_b=g.away_ml,
                    best_a=g.home_ml, best_b=g.away_ml, n_books=1,
                ),
                model_home_win=g.home_prob,
                favorite=g.favorite,
            ),
            shrink=shrink, cal=cal, tau=tau_v, markets=("moneyline",),
        ))
    return out


def generate_parlays(
    games: list[GameForParlay],
    *,
    shrink: shrinkage.EdgeShrink | None = None,
    cal: shrinkage.PlattMap | None = None,
    strict: bool = False,
    top_n: int | None = None,
) -> list[ParlayTicket]:
    """Legacy entry point: rank moneyline combinations over the given games.

    ``strict`` defaults to ``False`` here (unlike the new search) so the
    historical backtest can still measure what the engine *would* have said on
    every slate, including the slates where nothing qualifies.
    """
    n = len(games)
    if not MIN_LEGS <= n <= MAX_LEGS:
        raise ValueError(f"A parlay requires {MIN_LEGS}..{MAX_LEGS} legs (got {n})")
    pool = legs_from_legacy(games, shrink=shrink, cal=cal)
    if len({leg.event_id for leg in pool}) < n:
        return []
    # Default to every winner combination, preserving the old 2**N contract.
    return search_parlays(
        pool, n_legs=n, top_n=(top_n if top_n is not None else 2 ** n), strict=strict,
        require_events=[g.event_id for g in games],
        # No diversity cap here: the caller named the games and expects to see
        # every combination ranked, not a spread-out selection of them.
        max_leg_reuse=10 ** 6,
    )
