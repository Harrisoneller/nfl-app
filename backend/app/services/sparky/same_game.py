"""Same-game parlays, priced off the joint outcome distribution.

Why this module has to exist
----------------------------
:mod:`correlation` prices a ticket by integrating over *estimation* error — the
chance our numbers are wrong in a correlated way across games. That is the right
model for legs in different stadiums, whose outcomes really are close to
independent once you condition on our parameters being right.

It is the wrong model, by an order of magnitude, for two legs in the *same*
game. "Chiefs -3.5" and "Over 47" are not two bets that happen to share a model
error; they are two events on one bivariate random variable. When the favourite
covers a big number the total tends to run high, because the points that built
the cover are in the total. Multiplying the two marginals — which is what every
naive builder does, and what a factor model over estimation error is nearly
equivalent to here — misprices the pair by several points of probability.

``correlation.assert_distinct_units`` therefore refuses same-game legs and
points here. This module is the promised implementation.

How it works
------------
1. **A discrete joint lattice over (margin, total).** Margin comes from
   :meth:`dist_model.GameDistribution.margin_pmf`, which already carries NFL
   key-number excess at 3, 7, 10, 14 — so push mass is real rather than assumed.
   The total, conditional on a margin, is the bivariate Normal's conditional
   Normal integrated over each integer bin. Every leg in the game is then a
   deterministic function of the cell ``(m, t)``: win, push or loss. Nothing is
   approximated about the dependence — it is read straight off the lattice.

   Margin ``0`` is kept. NFL regular-season games can still tie after overtime,
   so a continuous Normal integrated over the ``[-0.5, 0.5]`` bin is a real
   outcome: moneyline can push, and deleting the cell would misprice pick'ems.

2. **Iterative proportional fitting onto the single-leg numbers.** The raw model
   lattice is *not* what we bet on. Every leg on the board has already been
   calibrated (Platt) and shrunk toward the market by the fitted ``lambda`` that
   says how much of our disagreement history supports — see :mod:`shrinkage`.
   If the SGP priced off the raw lattice, the same leg would carry one
   probability on the value board and a different one inside a parlay, and the
   difference would be pure untrusted edge, reappearing exactly where it is
   least visible.

   So the lattice is reweighted by IPF until each selected leg's implied
   ``(win, push)`` equals the leg's post-shrinkage ``(prob, push_prob)``. IPF
   changes the marginals to order and leaves the dependence structure (the odds
   ratios between cells) untouched — which is precisely the split we want:
   **marginals from the trusted single-leg pipeline, dependence from the model.**

3. **Push-pattern branches.** A same-game ticket does not have two outcomes. If
   one of three legs pushes, the ticket re-prices at two legs and can still pay.
   Walking the lattice gives, exactly, the probability of every push pattern and
   the probability that the surviving legs all win given that pattern. That is
   handed to :mod:`correlation` as a :class:`~correlation.PricedUnit`, which
   integrates it against the cross-game latent factor like any other leg.

The honesty problem, stated plainly
-----------------------------------
We can price the *probability* of a same-game combination. We cannot observe its
*price*: books do not pay the multiplied leg prices on correlated legs — they
run their own correlation model and quote a shaded number, and that number is
not in any feed this app has. Reporting EV against the multiplied price would
therefore be quoting an edge on a bet nobody can place.

So the output leads with :attr:`SameGameQuote.fair_decimal` — the price at which
this exact combination is break-even given our joint probability — and
:attr:`shade_room`, how far below the multiplied price a book can quote before
the bet stops being worth making. The user compares that to the book's actual
SGP quote. That is a usable edge; a fabricated EV is not.

Pure module: no DB, no param reads at import time.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Sequence

from .. import prediction_dist as pd_
from . import correlation as corr_mod
from .legs import MARKET_MONEYLINE, MARKET_SPREAD, MARKET_TOTAL, LegCandidate
from .odds_math import clamp

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .. import dist_model

WIN, PUSH, LOSS = 1, 0, -1

#: Margin lattice bounds. Wide enough that the truncated tails carry < 1e-9.
MARGIN_LO, MARGIN_HI = -84, 84
#: Total lattice bounds, in points. NFL totals live well inside this.
TOTAL_LO, TOTAL_HI = 0, 140

#: IPF stopping rule.
_IPF_MAX_ITERS = 200
_IPF_TOL = 1e-10

#: A same-game unit's estimation error does not diversify across its legs — the
#: legs share one game's model error — so the unit inherits the largest leg tau
#: rather than an RMS that would pretend they average out.
_TAU_AGG = max


class SameGameError(ValueError):
    """Raised when a same-game combination cannot be priced honestly."""


# --------------------------------------------------------------------------- #
# Leg -> lattice predicate
# --------------------------------------------------------------------------- #


def outcome_for(leg: LegCandidate, margin: int, total: int) -> int:
    """Settle ``leg`` on a hypothetical final ``(margin, total)``.

    ``margin`` is home minus away. Spread lines follow the sportsbook
    convention already used everywhere else in this codebase: the stored
    ``line`` is the handicap applied to the side named by ``leg.side``, so a
    home leg at ``-7.5`` wins when ``margin - 7.5 > 0``.
    """
    market = leg.market
    if market == MARKET_MONEYLINE:
        if margin == 0:
            return PUSH          # deleted from the lattice; kept for totality
        home_wins = margin > 0
        return WIN if (home_wins == (leg.side == "home")) else LOSS

    if market == MARKET_SPREAD:
        line = float(leg.line if leg.line is not None else 0.0)
        signed = (margin if leg.side == "home" else -margin) + line
        if abs(signed) < 1e-9:
            return PUSH
        return WIN if signed > 0 else LOSS

    if market == MARKET_TOTAL:
        line = float(leg.line if leg.line is not None else 0.0)
        diff = total - line
        if abs(diff) < 1e-9:
            return PUSH
        over = diff > 0
        return WIN if (over == (leg.side == "over")) else LOSS

    raise SameGameError(f"leg {leg.key!r}: unknown market {market!r}")


# --------------------------------------------------------------------------- #
# The joint lattice
# --------------------------------------------------------------------------- #


@dataclass
class JointLattice:
    """Discrete joint over integer ``(margin, total)`` for one game."""

    margins: tuple[int, ...]
    totals: tuple[int, ...]
    #: ``weight[i][j]`` = P(margin == margins[i], total == totals[j]).
    weight: list[list[float]]

    def mass(self) -> float:
        return sum(sum(row) for row in self.weight)

    def normalize(self) -> None:
        z = self.mass()
        if z <= 0:
            raise SameGameError("joint lattice collapsed to zero mass")
        for row in self.weight:
            for j in range(len(row)):
                row[j] /= z


def build_lattice(
    dist: "dist_model.GameDistribution",
    *,
    margin_lo: int = MARGIN_LO,
    margin_hi: int = MARGIN_HI,
    total_lo: int = TOTAL_LO,
    total_hi: int = TOTAL_HI,
) -> JointLattice:
    """P(margin = m, total = t) on the integer grid.

    NFL regular-season games can tie, so margin 0 is kept (unlike CFB, which
    plays overtime to a winner). The moneyline then has a real push mass.

    The margin marginal is taken from ``dist.margin_pmf`` so the key-number
    excess survives; the total is the bivariate Normal's conditional
    ``T | M = m``, integrated bin by bin. Building it the other way round (a
    total marginal with a conditional margin) would throw away the key numbers,
    which are the whole reason the discrete layer exists.
    """
    pmf_m = dist.margin_pmf(lo=margin_lo, hi=margin_hi)
    sm, st, rho = dist.sigma_m, dist.sigma_t, dist.rho
    if sm <= 0 or st <= 0:
        raise SameGameError("degenerate game distribution (sigma <= 0)")
    cond_sd = st * math.sqrt(max(1e-12, 1.0 - rho * rho))
    slope = rho * st / sm

    margins = tuple(range(margin_lo, margin_hi + 1))
    totals = tuple(range(total_lo, total_hi + 1))
    weight: list[list[float]] = []
    for m in margins:
        pm = pmf_m.get(m, 0.0)
        mu_t = dist.mu_t + slope * (m - dist.mu_m)
        row: list[float] = []
        if pm <= 0.0:
            weight.append([0.0] * len(totals))
            continue
        lo_cdf = pd_.norm_cdf((totals[0] - 0.5 - mu_t) / cond_sd)
        for t in totals:
            hi_cdf = pd_.norm_cdf((t + 0.5 - mu_t) / cond_sd)
            row.append(pm * max(hi_cdf - lo_cdf, 0.0))
            lo_cdf = hi_cdf
        weight.append(row)

    lat = JointLattice(margins=margins, totals=totals, weight=weight)
    lat.normalize()
    _rake_to_marginals(lat, pmf_m, _total_pmf(dist, totals))
    return lat


def _total_pmf(
    dist: "dist_model.GameDistribution", totals: tuple[int, ...],
) -> dict[int, float]:
    """The total's own integer marginal, integrated bin by bin.

    No key-number excess is layered on. NFL totals do cluster mildly, but
    this codebase has never measured it, and inventing a correction would put an
    unearned number straight into a price.
    """
    out: dict[int, float] = {}
    lo_cdf = pd_.norm_cdf((totals[0] - 0.5 - dist.mu_t) / dist.sigma_t)
    for t in totals:
        hi_cdf = pd_.norm_cdf((t + 0.5 - dist.mu_t) / dist.sigma_t)
        out[t] = max(hi_cdf - lo_cdf, 0.0)
        lo_cdf = hi_cdf
    z = sum(out.values())
    return {k: v / z for k, v in out.items()} if z > 0 else out


def _rake_to_marginals(
    lattice: JointLattice,
    pmf_m: dict[int, float],
    pmf_t: dict[int, float],
    *,
    iters: int = 12,
) -> None:
    """Restore both marginals after the conditional construction.

    Two things perturb them, and neither should be allowed to reach a price:

    * **The key-number layer.** ``margin_pmf`` adds excess mass at 3 and 7
      (and smaller spikes at 6, 10, 14) — small margins. With a positive
      margin/total correlation, moving margin mass inward drags the
      conditional total mean down with it, and the grid ends up quoting a
      total probability roughly half a point below what ``over_prob`` says.
      That would put one number on the totals leg here and a different one
      on the value board.
    * **Keeping the tie.** Margin 0 is a real NFL outcome. Raking still has
      to restore both marginals after the conditional construction so the
      total's mean is not dragged by the key-number layer.

    Raking (two-way IPF) scales rows and columns alternately until both target
    marginals hold. Like the leg fit below it preserves every odds ratio in the
    table, so the dependence structure — the only thing this lattice is really
    for — comes through untouched.
    """
    n_m, n_t = len(lattice.margins), len(lattice.totals)
    tgt_m = [pmf_m.get(m, 0.0) for m in lattice.margins]
    z = sum(tgt_m)
    if z <= 0:
        return
    tgt_m = [v / z for v in tgt_m]
    tgt_t = [pmf_t.get(t, 0.0) for t in lattice.totals]
    z = sum(tgt_t)
    if z <= 0:
        return
    tgt_t = [v / z for v in tgt_t]

    for _ in range(iters):
        for i in range(n_m):
            row = lattice.weight[i]
            got = sum(row)
            if got > 1e-15:
                sc = tgt_m[i] / got
                for j in range(n_t):
                    row[j] *= sc
        for j in range(n_t):
            got = sum(lattice.weight[i][j] for i in range(n_m))
            if got > 1e-15:
                sc = tgt_t[j] / got
                for i in range(n_m):
                    lattice.weight[i][j] *= sc
    lattice.normalize()


# --------------------------------------------------------------------------- #
# IPF: make the lattice agree with the single-leg board
# --------------------------------------------------------------------------- #


@dataclass
class IpfReport:
    iterations: int
    max_error: float
    converged: bool
    adjusted: list[dict[str, float]] = field(default_factory=list)


def fit_marginals(
    lattice: JointLattice,
    legs: Sequence[LegCandidate],
    *,
    max_iters: int = _IPF_MAX_ITERS,
    tol: float = _IPF_TOL,
) -> IpfReport:
    """Reweight ``lattice`` in place so each leg's implied (win, push) matches it.

    Each leg imposes a three-cell marginal constraint — win / push / loss — and
    IPF cycles through them scaling whole cell-classes. Because every update is
    a positive rescaling of a partition, all conditional odds ratios between
    cells inside a class are preserved: the marginals move, the dependence
    structure does not. That is the entire justification for using the model's
    lattice with the market-shrunk marginals bolted on.

    A target that the lattice cannot express is clamped rather than chased. The
    common case is a leg whose target push mass is zero (a half-point line) or
    whose lattice push mass is zero; asking IPF to move mass into an empty class
    would divide by zero and silently produce garbage.
    """
    n_m, n_t = len(lattice.margins), len(lattice.totals)
    # Precompute each leg's outcome class per cell once. This is the hot loop.
    classes: list[list[list[int]]] = []
    for leg in legs:
        grid = [
            [outcome_for(leg, m, t) for t in lattice.totals]
            for m in lattice.margins
        ]
        classes.append(grid)

    targets: list[tuple[float, float, float]] = []
    adjusted: list[dict[str, float]] = []
    for leg in legs:
        # `leg.prob` is conditional on no push; the lattice settles real
        # outcomes, so the target has to be unconditional. Using `leg.prob`
        # directly would credit a whole-number leg with its push mass twice.
        push = clamp(float(leg.push_prob), 0.0, 1.0)
        win = clamp(leg.unconditional_win_prob, 0.0, max(0.0, 1.0 - push))
        loss = max(0.0, 1.0 - win - push)
        targets.append((win, push, loss))

    report = IpfReport(iterations=0, max_error=1.0, converged=False)
    for it in range(1, max_iters + 1):
        max_err = 0.0
        for li, leg in enumerate(legs):
            grid = classes[li]
            got = [0.0, 0.0, 0.0]          # index by outcome + 1 -> loss/push/win
            for i in range(n_m):
                row, crow = lattice.weight[i], grid[i]
                for j in range(n_t):
                    got[crow[j] + 1] += row[j]
            want = targets[li]
            scale = [1.0, 1.0, 1.0]
            for oc, (g, w) in enumerate(
                ((got[2], want[0]), (got[1], want[1]), (got[0], want[2]))
            ):
                idx = (2, 1, 0)[oc]
                if g <= 1e-15:
                    # Class is unreachable on this lattice. Do not divide; note
                    # it and let the remaining classes absorb the mass.
                    scale[idx] = 1.0
                    if w > 1e-9:
                        max_err = max(max_err, w)
                    continue
                scale[idx] = w / g
                max_err = max(max_err, abs(g - w))
            for i in range(n_m):
                row, crow = lattice.weight[i], grid[i]
                for j in range(n_t):
                    s = scale[crow[j] + 1]
                    if s != 1.0:
                        row[j] *= s
        lattice.normalize()
        report.iterations = it
        report.max_error = max_err
        if max_err < tol:
            report.converged = True
            break

    # Report what the fit actually achieved per leg, so the payload can show it.
    for li, leg in enumerate(legs):
        grid = classes[li]
        got = [0.0, 0.0, 0.0]
        for i in range(n_m):
            row, crow = lattice.weight[i], grid[i]
            for j in range(n_t):
                got[crow[j] + 1] += row[j]
        adjusted.append({
            "key": leg.key,
            "target_win": round(targets[li][0], 6),
            "fitted_win": round(got[2], 6),
            "target_push": round(targets[li][1], 6),
            "fitted_push": round(got[1], 6),
        })
    report.adjusted = adjusted
    return report


# --------------------------------------------------------------------------- #
# Branch enumeration
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SameGameQuote:
    """A priced same-game combination."""

    event_id: str
    leg_keys: tuple[str, ...]
    unit: "corr_mod.PricedUnit"

    #: P(the ticket returns at least the stake) and P(every leg wins outright).
    survive_prob: float
    all_win_prob: float
    #: Probability of at least one push (the ticket re-prices smaller).
    push_any_prob: float

    #: Gross multiple if the book simply multiplies the leg prices.
    multiplied_decimal: float
    #: Our E[return per unit] **at that multiplied price**. Shown as a
    #: hypothetical, never as the EV of a placeable bet — see the module
    #: docstring.
    expected_return_at_multiplied: float
    #: What naive independent multiplication of the leg probabilities says.
    independent_expected_return: float
    #: expected_return_at_multiplied - independent_expected_return. Positive
    #: means the legs help each other and a book multiplying them is underpaying
    #: itself; negative means the combination is worth less than it looks.
    correlation_effect: float

    #: The break-even price for this exact combination. THIS is the headline.
    fair_decimal: float
    fair_american: int
    #: 1 - fair_decimal / multiplied_decimal. How much a book may shade the
    #: multiplied price before the bet stops being worth making. Negative means
    #: the multiplied price is already not enough.
    shade_room: float

    ipf: IpfReport | None = None
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "leg_keys": list(self.leg_keys),
            "survive_prob": round(self.survive_prob, 5),
            "all_win_prob": round(self.all_win_prob, 5),
            "push_any_prob": round(self.push_any_prob, 5),
            "multiplied_decimal": round(self.multiplied_decimal, 4),
            "expected_return_at_multiplied": round(self.expected_return_at_multiplied, 5),
            "independent_expected_return": round(self.independent_expected_return, 5),
            "correlation_effect": round(self.correlation_effect, 5),
            "fair_decimal": round(self.fair_decimal, 4),
            "fair_american": self.fair_american,
            "shade_room": round(self.shade_room, 5),
            "ipf_converged": None if self.ipf is None else self.ipf.converged,
            "ipf_max_error": None if self.ipf is None else round(self.ipf.max_error, 9),
            "notes": list(self.notes),
        }


def _branches(
    lattice: JointLattice, legs: Sequence[LegCandidate],
) -> list["corr_mod.Branch"]:
    """Collapse the lattice into push-pattern branches.

    A branch is one pattern of pushed legs. Its ``mass`` is the probability of
    that pattern occurring at all; its ``win_prob`` is the conditional
    probability that every *live* leg in it wins; its ``multiplier`` is the
    payout if they do (pushed legs return their stake, i.e. contribute 1.0).
    """
    n_m, n_t = len(lattice.margins), len(lattice.totals)
    grids = [
        [[outcome_for(leg, m, t) for t in lattice.totals] for m in lattice.margins]
        for leg in legs
    ]
    # pattern -> [total mass, mass where all live legs win]
    acc: dict[tuple[int, ...], list[float]] = {}
    for i in range(n_m):
        w_row = lattice.weight[i]
        for j in range(n_t):
            w = w_row[j]
            if w <= 0.0:
                continue
            pattern = []
            all_live_win = True
            for li in range(len(legs)):
                oc = grids[li][i][j]
                pattern.append(1 if oc == PUSH else 0)
                if oc == LOSS:
                    all_live_win = False
            key = tuple(pattern)
            slot = acc.setdefault(key, [0.0, 0.0])
            slot[0] += w
            if all_live_win:
                slot[1] += w

    out: list[corr_mod.Branch] = []
    for pattern, (mass, win_mass) in acc.items():
        if mass <= 0.0:
            continue
        mult = 1.0
        for li, pushed in enumerate(pattern):
            if not pushed:
                mult *= legs[li].decimal_odds
        out.append(corr_mod.Branch(
            mass=mass,
            multiplier=mult,
            win_prob=clamp(win_mass / mass, 0.0, 1.0),
        ))
    out.sort(key=lambda b: -b.mass)
    return out


def _aggregate_loadings(legs: Sequence[LegCandidate]) -> tuple[float, ...]:
    """One loading vector for the whole game-unit.

    The legs of a same-game ticket share the game, so their exposures to the
    slate-level channels are averaged rather than summed — a unit is still one
    game's worth of exposure to "are our numbers right today", however many legs
    are stacked inside it. The vector is renormalised down if the average lands
    outside the unit ball, which it cannot here but is cheap to guarantee.
    """
    n = max(1, len(legs))
    dims = max((len(leg.loadings) for leg in legs), default=corr_mod.N_CHANNELS)
    out = [0.0] * dims
    for leg in legs:
        for k, v in enumerate(leg.loadings):
            out[k] += v / n
    norm_sq = sum(v * v for v in out)
    cap = 0.95
    if norm_sq > cap * cap:
        s = cap / math.sqrt(norm_sq)
        out = [v * s for v in out]
    return tuple(out)


def price_same_game(
    dist: "dist_model.GameDistribution",
    legs: Sequence[LegCandidate],
    *,
    fit: bool = True,
    max_legs: int = 4,
) -> SameGameQuote:
    """Price a set of legs from one game against the joint distribution.

    ``legs`` must all share an ``event_id`` and must be distinct sides — the
    caller is responsible for rejecting a both-sides-of-one-market pair, which
    is not a parlay but a (losing) arbitrage on the vig.
    """
    if not legs:
        raise SameGameError("a same-game combination needs at least one leg")
    if len(legs) > max_legs:
        raise SameGameError(
            f"same-game combinations are capped at {max_legs} legs "
            f"(got {len(legs)}); beyond that the lattice fit is doing more "
            f"work than the price can support"
        )
    event_ids = {leg.event_id for leg in legs}
    if len(event_ids) != 1:
        raise SameGameError(f"price_same_game got legs from {len(event_ids)} games")

    _reject_contradictions(legs)

    lattice = build_lattice(dist)
    report = fit_marginals(lattice, legs) if fit else None

    branches = _branches(lattice, legs)
    if not branches:
        raise SameGameError("no branch carried any probability mass")

    multiplied = 1.0
    for leg in legs:
        multiplied *= leg.decimal_odds

    unit = corr_mod.PricedUnit(
        key="+".join(leg.key for leg in legs),
        event_id=next(iter(event_ids)),
        decimal_odds=multiplied,
        tau=_TAU_AGG(float(leg.tau) for leg in legs),
        loadings=_aggregate_loadings(legs),
        branches=tuple(branches),
        leg_keys=tuple(leg.key for leg in legs),
    )

    survive = sum(b.mass * b.win_prob for b in branches)
    all_win = sum(
        b.mass * b.win_prob for b in branches if abs(b.multiplier - multiplied) < 1e-9
    )
    push_any = sum(b.mass for b in branches if abs(b.multiplier - multiplied) >= 1e-9)

    e_return = unit.expected_return()

    independent = 1.0
    for leg in legs:
        independent *= (
            leg.unconditional_win_prob * leg.decimal_odds
            + clamp(leg.push_prob, 0.0, 1.0)
        )

    fair_dec = fair_decimal(unit)
    shade = 1.0 - fair_dec / multiplied if multiplied > 0 else 0.0

    notes: list[str] = []
    if report is not None and not report.converged:
        notes.append(
            "leg probabilities could not be matched exactly on the lattice; "
            "the joint is approximate"
        )
    if push_any > 0.02:
        notes.append(
            f"{push_any:.1%} chance at least one leg pushes and the ticket "
            f"re-prices smaller"
        )

    return SameGameQuote(
        event_id=unit.event_id,
        leg_keys=unit.leg_keys,
        unit=unit,
        survive_prob=survive,
        all_win_prob=all_win,
        push_any_prob=push_any,
        multiplied_decimal=multiplied,
        expected_return_at_multiplied=e_return,
        independent_expected_return=independent,
        correlation_effect=e_return - independent,
        fair_decimal=fair_dec,
        fair_american=_decimal_to_american(fair_dec),
        shade_room=shade,
        ipf=report,
        notes=tuple(notes),
    )


def _reject_contradictions(legs: Sequence[LegCandidate]) -> None:
    """Refuse combinations that cannot all win, or that are the same bet twice.

    Two legs on opposite sides of one market have joint win probability zero;
    two legs on the *same* side of one market at different lines are one
    opinion sold twice. Both are worth naming explicitly, because the lattice
    would happily price the first at ~0 and the user would be left guessing why.
    """
    seen: dict[tuple[str, str, float | None], str] = {}
    by_market: dict[str, list[LegCandidate]] = {}
    for leg in legs:
        sig = (leg.market, leg.side, leg.line)
        if sig in seen:
            raise SameGameError(
                f"duplicate leg: {leg.label} is already in this ticket"
            )
        seen[sig] = leg.key
        by_market.setdefault(leg.market, []).append(leg)

    for market, group in by_market.items():
        sides = {leg.side for leg in group}
        if len(sides) > 1:
            labels = " and ".join(sorted(leg.label for leg in group))
            raise SameGameError(
                f"{labels} are opposite sides of the same {market} — they "
                f"cannot both win, so this is not a parlay"
            )
        if len(group) > 1:
            # Same market, same side, two numbers. The legs are nested — the
            # harder one winning implies the easier one does — so the pair is
            # one opinion sold twice at a worse combined price, never a parlay.
            # The lattice would price it correctly and it would still be a bad
            # bet; saying why is more useful than quoting the number.
            labels = " and ".join(sorted(leg.label for leg in group))
            raise SameGameError(
                f"{labels} are the same {market} bet at two different numbers. "
                f"One implies the other, so stacking them adds vig without "
                f"adding an opinion — take the better number on its own."
            )
    # Moneyline stacked with a spread on the same team is legal and common
    # (it is a leveraged version of one opinion); the lattice prices the
    # near-perfect dependence correctly, so it is allowed, not refused.


def fair_decimal(
    unit: "corr_mod.PricedUnit", *, tol: float = 1e-10, max_iter: int = 200,
) -> float:
    """The gross decimal price at which ``unit`` is exactly break-even.

    The unit's branch multipliers are scaled by a common factor ``k`` — which is
    what a book does when it reprices a correlated combination — and ``k`` is
    solved for ``E[return] = 1`` by bisection. Pushed legs are *not* scaled:
    a pushed leg returns its own stake regardless of what the book quotes for
    the rest, so scaling that part too would misstate the break-even by the
    push mass.
    """
    def e_return(k: float) -> float:
        total = 0.0
        for b in unit.branches:
            live = b.multiplier
            scaled = 1.0 + (live - 1.0) * k if live > 1.0 else live
            total += b.mass * b.win_prob * scaled
        return total

    lo, hi = 1e-6, 1.0
    if e_return(hi) < 1.0:
        # Even the full multiplied price is not enough; extend upward to find
        # the price that would be, so `shade_room` can go negative and say so.
        hi = 1.0
        for _ in range(60):
            hi *= 1.5
            if e_return(hi) >= 1.0:
                break
        else:
            return float("inf")
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        if e_return(mid) < 1.0:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    k = 0.5 * (lo + hi)
    return 1.0 + (unit.decimal_odds - 1.0) * k


def _decimal_to_american(dec: float) -> int:
    if not math.isfinite(dec) or dec <= 1.0:
        return 0
    from .odds_math import decimal_to_american
    return decimal_to_american(dec)
