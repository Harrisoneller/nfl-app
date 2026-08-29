"""Correlation- and push-aware parlay pricing.

Why this module exists
----------------------
The original engine priced a parlay as ``prod(p_i)`` and documented the choice
as "football game outcomes are close enough to independent". That is wrong in
two separate, opposite-signed ways, and both matter more than the vig:

1. **Push is not a binary outcome.** A spread or total leg on a whole number
   can push, which *voids the leg* and re-prices the whole ticket at N-1 legs.
   A win/lose model cannot represent that, so every spread/total parlay it
   prices is wrong by roughly the push probability times the payout delta.

2. **Legs are not independent — but not because the games are.** Two different
   Sunday games are, as outcomes, very nearly independent. What is *not*
   independent is **our estimate of them**. Every leg is priced off the same
   ratings, the same opponent-adjustment ridge, the same market blend and the
   same calibration map. If those are off today, they are off on every leg at
   once. A ticket is therefore a bet on the model being right, N times, and
   that is a single correlated bet — not N independent ones.

   There is also a real (small) outcome-level channel: a slate shares a
   scoring environment (weather systems, officiating, rule regime), so
   over/over is genuinely positively correlated across games, as is
   favorite/favorite in a week where chalk holds.

Which direction does correlation move the price?
------------------------------------------------
Not always the same way, so the engine computes it rather than applying a
guessed haircut. Each leg's conditional probability ``q_i(F)`` is a monotone
function of the shared factor ``F``, so the legs are *positively associated*
when their loadings share a sign and negatively associated when they oppose.
By the association (FKG) inequality:

  - **All legs leaning the same way** — every leg is a bet on our model being
    right, or every leg is an over — then ``E[prod q_i] > prod E[q_i]``. The
    ticket is worth **more** than the book's multiplication implies, at every
    leg count. This is the actual edge mechanism in a parlay, and it is
    precisely what a book pricing by multiplying its own vigged numbers
    ignores.
  - **Legs pulling against each other** — an over stacked with an under, or a
    leg taking the model's side stacked with one taking the market's — then
    the inequality flips and the ticket is worth **less** than the product.
    A naive engine would happily sell that ticket at a fabricated edge.

Both effects are reported explicitly (``correlation_effect``) against the
naive-independent baseline, so the number is auditable rather than asserted.

Marginals are preserved exactly
-------------------------------
A subtle trap: if you write ``p_i(F) = sigmoid(logit(p_i) + tau * F)`` then
``E[p_i(F)] < p_i`` for ``p_i > 0.5`` (Jensen — sigmoid is concave above the
midpoint). Uncertainty would then silently shrink every favorite, double
-counting whatever the calibration map already absorbed. Instead we *re-center*
the latent mean,

    L_i = logit(p_i) * sqrt(1 + (pi/8) * tau_i^2)

so that ``E[p_i(F, e)] == p_i`` to the accuracy of the MacKay probit-logit
approximation. Uncertainty then changes only the *dependence* between legs,
never the marginal. Conservatism belongs in :mod:`shrinkage`, which lowers
``p_i`` itself for reasons we can defend; it does not belong here as an
accidental side effect of the integration scheme.

Correlation structure
---------------------
Legs carry ``loadings``: a vector of exposures to independent latent channels.
Three channels are used by the leg builder:

  0. **model error** — are our numbers right today? Every leg loads on this.
  1. **favorite / market regime** — does chalk hold this slate? ML and spread
     legs load with the sign of the side taken; total legs do not.
  2. **scoring environment** — is the slate high- or low-scoring? Total legs
     load with the sign of over/under; ML legs do not (a spread leg loads
     weakly, since scoring environment moves margin variance).

The implied latent correlation matrix is ``C_ij = dot(loadings_i, loadings_j)``.
Integrating a K-dimensional Gaussian per candidate ticket is far too slow for a
slate-wide search, so we collapse ``C`` to its best rank-1 approximation by
power iteration (N <= 8, so this is microseconds) and integrate one dimension
with 15-node Gauss-Hermite quadrature. :func:`rank1_residual` reports how much
of ``C`` the collapse missed, and the exhaustive K-dimensional path is kept in
:func:`expected_return_exact` purely as a test oracle.

Same-game legs are **rejected**, not approximated: two legs from one game are
correlated through the actual joint outcome distribution, not through estimator
error, and must be priced with ``dist_model.GameDistribution.joint_prob``. See
:func:`assert_distinct_games`.

Pure module: no DB, no network, no third-party imports.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .odds_math import clamp, inv_logit, logit

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: Number of independent latent channels a leg may load on.
N_CHANNELS = 3
CH_MODEL = 0
CH_FAVORITE = 1
CH_SCORING = 2

#: MacKay's probit-logit constant: E_Z[sigmoid(a + bZ)] ~= sigmoid(a / sqrt(1 + PI_8 * b^2)).
_PI_8 = math.pi / 8.0

#: 15-node Gauss-Hermite quadrature in *probabilists'* form: nodes/weights such
#: that ``sum(w_k * f(x_k)) ~= E[f(Z)]`` for ``Z ~ N(0, 1)``. Verified to
#: reproduce E[Z^2] = 1 and E[Z^4] = 3 to machine precision.
_GH_NODES: tuple[tuple[float, float], ...] = (
    (-6.363947888829839, 8.589649899633252e-10),
    (-5.190093591304781, 5.975419597920599e-07),
    (-4.196207711269016, 5.642146405189029e-05),
    (-3.289082424398766, 0.001567357503549956),
    (-2.432436827009758, 0.017365774492137616),
    (-1.606710069028730, 0.089417795399844370),
    (-0.799129068324548, 0.232462293609732250),
    (0.0, 0.318259518259518150),
    (0.799129068324548, 0.232462293609732250),
    (1.606710069028730, 0.089417795399844370),
    (2.432436827009758, 0.017365774492137616),
    (3.289082424398766, 0.001567357503549956),
    (4.196207711269016, 5.642146405189029e-05),
    (5.190093591304781, 5.975419597920599e-07),
    (6.363947888829839, 8.589649899633252e-10),
)

#: Coarser rule used on the exact (per-channel) path, where the node count is
#: cubed. Nine central nodes reproduce E[Z^2] and E[Z^4] to ~1e-9, which is far
#: tighter than anything downstream cares about.
_EXACT_NODES: tuple[tuple[float, float], ...] = tuple(
    (x, w / sum(ww for _, ww in _GH_NODES[3:12]))
    for x, w in _GH_NODES[3:12]
)

#: Loadings are clipped so the idiosyncratic share never collapses to zero
#: (a leg with |loading| == 1 has no independent information at all).
_MAX_LOADING = 0.98


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PricedLeg:
    """The minimum a leg must supply to be priced inside a parlay.

    ``prob`` and ``push_prob`` are the *post-calibration, post-shrinkage*
    numbers — this module does not second-guess them, it only combines them.
    ``prob + push_prob`` must be <= 1; the remainder is the loss probability.
    """

    key: str                       # stable identifier, unique within a ticket
    event_id: str                  # game the leg belongs to (used for the same-game guard)
    prob: float                    # P(leg wins)
    push_prob: float               # P(leg pushes / voids) — 0 for moneyline and half-points
    decimal_odds: float            # payout multiple on a win (total return per unit)
    tau: float = 0.0               # logit-scale sd of our estimation error for `prob`
    loadings: tuple[float, ...] = field(default=(0.0,) * N_CHANNELS)

    def __post_init__(self) -> None:
        if not 0.0 <= self.prob <= 1.0:
            raise ValueError(f"leg {self.key}: prob must be in [0, 1] (got {self.prob})")
        if not 0.0 <= self.push_prob <= 1.0:
            raise ValueError(f"leg {self.key}: push_prob must be in [0, 1] (got {self.push_prob})")
        if self.prob + self.push_prob > 1.0 + 1e-9:
            raise ValueError(
                f"leg {self.key}: prob + push_prob must be <= 1 "
                f"(got {self.prob} + {self.push_prob})"
            )
        if self.decimal_odds <= 1.0:
            raise ValueError(f"leg {self.key}: decimal_odds must be > 1 (got {self.decimal_odds})")
        if self.tau < 0.0:
            raise ValueError(f"leg {self.key}: tau must be >= 0 (got {self.tau})")
        norm = sum(x * x for x in self.loadings)
        if norm > 1.0 + 1e-9:
            raise ValueError(
                f"leg {self.key}: sum of squared loadings must be <= 1 (got {norm:.4f})"
            )

    @property
    def loss_prob(self) -> float:
        return max(0.0, 1.0 - self.prob - self.push_prob)


@dataclass(frozen=True)
class Branch:
    """One push pattern of a multi-leg unit.

    ``mass`` is the unconditional probability that this pattern of pushes
    happens at all; ``win_prob`` is the conditional probability that every
    *live* (non-pushed) leg in the pattern then wins; ``multiplier`` is the
    gross return per unit staked when they do — pushed legs return their own
    stake and so contribute a factor of exactly 1.

    A single leg is the two-branch case: ``(mass=1-push, mult=dec, win=q)`` and
    ``(mass=push, mult=1, win=1)``. Everything in this module is written
    against branches, and :func:`to_unit` is the only place the single-leg
    shape is special-cased.
    """

    mass: float
    multiplier: float
    win_prob: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.mass <= 1.0 + 1e-9:
            raise ValueError(f"branch mass must be in [0, 1] (got {self.mass})")
        if not 0.0 <= self.win_prob <= 1.0 + 1e-9:
            raise ValueError(f"branch win_prob must be in [0, 1] (got {self.win_prob})")
        if self.multiplier < 0.0:
            raise ValueError(f"branch multiplier must be >= 0 (got {self.multiplier})")


@dataclass(frozen=True)
class PricedUnit:
    """One game's contribution to a ticket — one leg, or a same-game bundle.

    The cross-game factor model treats a unit as atomic. That is the whole
    design: **dependence inside a game is priced by the outcome distribution**
    (:mod:`same_game`, which builds the branches), **dependence between games is
    priced by the latent estimation-error factor** (here). Mixing the two would
    either understate same-game correlation by an order of magnitude or invent
    cross-game correlation that does not exist.

    ``tau`` and ``loadings`` describe the unit's exposure to that latent factor.
    For a bundle they are aggregated from its legs — see
    ``same_game._aggregate_loadings``.
    """

    key: str
    event_id: str
    decimal_odds: float            # gross multiple if every leg in the unit wins
    branches: tuple[Branch, ...]
    tau: float = 0.0
    loadings: tuple[float, ...] = field(default=(0.0,) * N_CHANNELS)
    leg_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.branches:
            raise ValueError(f"unit {self.key}: needs at least one branch")
        total = sum(b.mass for b in self.branches)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"unit {self.key}: branch masses must sum to 1 (got {total:.9f})"
            )
        if self.decimal_odds <= 1.0:
            raise ValueError(
                f"unit {self.key}: decimal_odds must be > 1 (got {self.decimal_odds})"
            )
        if self.tau < 0.0:
            raise ValueError(f"unit {self.key}: tau must be >= 0 (got {self.tau})")
        norm = sum(x * x for x in self.loadings)
        if norm > 1.0 + 1e-9:
            raise ValueError(
                f"unit {self.key}: sum of squared loadings must be <= 1 (got {norm:.4f})"
            )

    @property
    def n_legs(self) -> int:
        return max(1, len(self.leg_keys))

    def expected_return(self) -> float:
        """E[return] for this unit alone, with no cross-game factor applied."""
        return sum(b.mass * b.win_prob * b.multiplier for b in self.branches)

    def survive_prob(self) -> float:
        return sum(b.mass * b.win_prob for b in self.branches)


def to_unit(leg: PricedLeg) -> PricedUnit:
    """Lift a single leg into the branch representation, exactly.

    The two branches reproduce :func:`_leg_factors` term for term, which is what
    makes the generalisation safe: a ticket of single legs prices identically
    before and after this refactor, and there is a test that pins it.
    """
    push = clamp(leg.push_prob, 0.0, 1.0)
    room = max(0.0, 1.0 - push)
    q = clamp(leg.prob / room, 0.0, 1.0) if room > 1e-12 else 0.0
    branches = [Branch(mass=room, multiplier=leg.decimal_odds, win_prob=q)]
    if push > 0.0:
        branches.append(Branch(mass=push, multiplier=1.0, win_prob=1.0))
    return PricedUnit(
        key=leg.key,
        event_id=leg.event_id,
        decimal_odds=leg.decimal_odds,
        branches=tuple(branches),
        tau=leg.tau,
        loadings=leg.loadings,
        leg_keys=(leg.key,),
    )


@dataclass
class ParlayPricing:
    """Everything the ranking layer needs, plus the receipts for why."""

    n_legs: int
    decimal_odds: float             # the ticket's gross payout multiple (all legs win)
    expected_return: float          # E[gross return per 1 unit staked], push-aware
    expected_value: float           # expected_return - 1
    survive_prob: float             # P(no leg loses)  — the ticket pays >= stake
    all_win_prob: float             # P(every leg wins outright, no pushes)
    return_sd: float                # sd of the gross return (for uncertainty-aware sizing)
    ev_estimation_sd: float         # sd of E[return] induced by *our own* parameter error
    # --- the audit trail -------------------------------------------------- #
    independent_expected_return: float   # what prod(p_i) pricing would have said
    independent_all_win_prob: float
    correlation_effect: float            # expected_return - independent_expected_return
    push_effect: float                   # E[return] - E[return with pushes treated as losses]
    mean_pairwise_corr: float            # average off-diagonal latent correlation
    rank1_residual: float                # how much the rank-1 collapse missed (0 = exact)
    effective_loadings: tuple[float, ...]

    def as_dict(self) -> dict:
        return {
            "n_legs": self.n_legs,
            "decimal_odds": round(self.decimal_odds, 4),
            "expected_return": round(self.expected_return, 5),
            "expected_value": round(self.expected_value, 5),
            "survive_prob": round(self.survive_prob, 5),
            "all_win_prob": round(self.all_win_prob, 5),
            "return_sd": round(self.return_sd, 4),
            "ev_estimation_sd": round(self.ev_estimation_sd, 5),
            "independent_expected_return": round(self.independent_expected_return, 5),
            "independent_all_win_prob": round(self.independent_all_win_prob, 5),
            "correlation_effect": round(self.correlation_effect, 5),
            "push_effect": round(self.push_effect, 5),
            "mean_pairwise_corr": round(self.mean_pairwise_corr, 4),
            "rank1_residual": round(self.rank1_residual, 4),
        }


# --------------------------------------------------------------------------- #
# Guards
# --------------------------------------------------------------------------- #


def assert_distinct_games(legs: list[PricedLeg]) -> None:
    """Reject two legs from one game on the *single-leg* path.

    Kept for the search path, which builds tickets out of bare legs and still
    enforces one leg per game. Two legs from one game are correlated through the
    *outcome* joint distribution (a team's moneyline and its team total are
    close to mechanically linked), not through estimator error, and pricing them
    with this factor model would understate the dependence by an order of
    magnitude — the single most common way a same-game parlay tool ends up
    recommending -EV tickets with a straight face.

    The supported route for same-game legs is
    ``same_game.price_same_game()``, which prices them on a discrete joint
    lattice and hands back one :class:`PricedUnit`.
    """
    assert_distinct_units([to_unit(leg) for leg in legs])


def assert_distinct_units(units: list[PricedUnit]) -> None:
    """One unit per game.

    Same-game *legs* are now supported — they are bundled into a single
    :class:`PricedUnit` by :mod:`same_game`, which prices their dependence off
    ``dist_model.GameDistribution`` rather than off estimation error. What is
    still refused is two separate **units** on one game, because that would put
    the same game through the factor model twice and price the strongest
    dependence in the ticket as if it were the weakest.
    """
    seen: set[str] = set()
    for unit in units:
        if unit.event_id in seen:
            raise ValueError(
                f"two separate units on game {unit.event_id!r}: same-game legs "
                f"must be bundled through same_game.price_same_game() before "
                f"pricing, not passed as independent units"
            )
        seen.add(unit.event_id)


# --------------------------------------------------------------------------- #
# Latent correlation structure
# --------------------------------------------------------------------------- #


def correlation_matrix(legs: list[PricedLeg]) -> list[list[float]]:
    """Latent correlation ``C_ij = dot(loadings_i, loadings_j)``, unit diagonal."""
    n = len(legs)
    c = [[0.0] * n for _ in range(n)]
    for i in range(n):
        c[i][i] = 1.0
        for j in range(i + 1, n):
            v = sum(a * b for a, b in zip(legs[i].loadings, legs[j].loadings))
            v = clamp(v, -0.999, 0.999)
            c[i][j] = c[j][i] = v
    return c


def rank1_loadings(corr: list[list[float]], *, iters: int = 200) -> tuple[float, ...]:
    """Best rank-1 fit ``b`` to the **off-diagonal** of ``corr``.

    We want ``b`` minimizing ``sum_{i != j} (corr_ij - b_i b_j)^2``. Note this
    is deliberately *not* the leading eigenvector of ``corr``: the diagonal is
    1.0 by construction (each leg's latent is unit-variance) and carries no
    information about dependence, so including it would inflate every loading
    toward 1 and massively overstate correlation.

    Alternating least squares gives the exact coordinate-wise minimizer and
    converges in a handful of sweeps at N <= 8:

        b_i  <-  sum_{j != i} corr_ij b_j  /  sum_{j != i} b_j^2

    Loadings are clipped to +/- 0.98 so every leg retains idiosyncratic
    variance. The overall sign of ``b`` is unidentified (F is symmetric about
    zero); it is normalized so the largest-magnitude entry is positive.
    """
    n = len(corr)
    if n == 0:
        return ()
    if n == 1:
        return (0.0,)

    # Initialize from the row-mean off-diagonal magnitude, signed by row sum.
    b = []
    for i in range(n):
        off = [corr[i][j] for j in range(n) if j != i]
        mag = sum(abs(v) for v in off) / len(off)
        sign = 1.0 if sum(off) >= 0 else -1.0
        b.append(sign * math.sqrt(min(mag, 1.0)))
    if all(abs(x) < 1e-9 for x in b):
        return tuple(0.0 for _ in range(n))

    for _ in range(iters):
        moved = 0.0
        for i in range(n):
            num = sum(corr[i][j] * b[j] for j in range(n) if j != i)
            den = sum(b[j] * b[j] for j in range(n) if j != i)
            if den < 1e-12:
                continue
            new = clamp(num / den, -_MAX_LOADING, _MAX_LOADING)
            moved = max(moved, abs(new - b[i]))
            b[i] = new
        if moved < 1e-10:
            break

    biggest = max(range(n), key=lambda i: abs(b[i]))
    if b[biggest] < 0:
        b = [-x for x in b]
    return tuple(b)


def rank1_residual(corr: list[list[float]], b: tuple[float, ...]) -> float:
    """Max absolute off-diagonal error of the rank-1 collapse (0.0 = exact)."""
    n = len(corr)
    worst = 0.0
    for i in range(n):
        for j in range(i + 1, n):
            worst = max(worst, abs(corr[i][j] - b[i] * b[j]))
    return worst


def mean_pairwise_correlation(corr: list[list[float]]) -> float:
    n = len(corr)
    if n < 2:
        return 0.0
    off = [corr[i][j] for i in range(n) for j in range(i + 1, n)]
    return sum(off) / len(off)


# --------------------------------------------------------------------------- #
# Conditional leg probability
# --------------------------------------------------------------------------- #


def _recentred_latent(prob: float, tau: float) -> float:
    """Latent mean ``L`` such that ``E[sigmoid(L + tau * Z)] == prob``.

    This is the fix that stops uncertainty from silently shrinking favorites.
    See the module docstring.
    """
    return logit(prob) * math.sqrt(1.0 + _PI_8 * tau * tau)


def _conditional_prob_multi(
    prob: float, tau: float, loadings: tuple[float, ...], fvec: tuple[float, ...],
) -> float:
    """``E_e[ P(leg wins | F = fvec, e) ]`` with the idiosyncratic part integrated out.

    ``loadings`` is the leg's exposure to each latent channel (length 1 after
    the rank-1 collapse, ``N_CHANNELS`` on the exact path). Whatever variance
    the loadings do not claim stays idiosyncratic and is absorbed analytically
    by MacKay's approximation instead of costing another quadrature dimension.
    """
    if tau <= 0.0:
        return prob
    shared = sum(ld * f for ld, f in zip(loadings, fvec))
    norm_sq = min(sum(ld * ld for ld in loadings), _MAX_LOADING ** 2)
    idio = tau * math.sqrt(max(0.0, 1.0 - norm_sq))
    latent = _recentred_latent(prob, tau) + tau * shared
    return inv_logit(latent / math.sqrt(1.0 + _PI_8 * idio * idio))


def conditional_prob(prob: float, tau: float, loading: float, f: float) -> float:
    """Single-factor form of :func:`_conditional_prob_multi` (kept for tests)."""
    return _conditional_prob_multi(
        prob, tau, (clamp(loading, -_MAX_LOADING, _MAX_LOADING),), (f,)
    )


# --------------------------------------------------------------------------- #
# Pricing
# --------------------------------------------------------------------------- #


def _no_push_prob(leg: PricedLeg) -> float:
    """``P(leg wins | leg does not push)`` — the quantity the latent factor moves.

    Push probability is a property of the *line* (how much scoring mass sits
    exactly on the number), not of which side is right, so it is held fixed
    under the factor and only the win/lose split inside the remaining mass is
    perturbed. Perturbing the unconditional win probability instead would let
    a confident leg's win mass eat into the push mass, which is nonsense.
    """
    room = max(1e-12, 1.0 - leg.push_prob)
    return clamp(leg.prob / room, 0.0, 1.0)


def _leg_factors(leg: PricedLeg, q_cond: float) -> tuple[float, float, float, float]:
    """Per-leg expectations given ``q_cond = P(win | no push)`` under the factor.

    Returns ``(E[return], E[return^2], P(no loss), P(win))`` for one leg, where
    the leg's contribution to the ticket's gross return is ``dec`` on a win,
    ``1.0`` on a push (that leg's stake rides through — the ticket re-prices at
    one fewer leg) and ``0.0`` on a loss. Because the ticket's return is the
    *product* of the legs' factors and outcomes are conditionally independent
    given the latent factor, taking these per-leg expectations and multiplying
    them is exact, not an approximation.
    """
    room = max(0.0, 1.0 - leg.push_prob)
    p = clamp(q_cond, 0.0, 1.0) * room
    dec = leg.decimal_odds
    e1 = p * dec + leg.push_prob
    e2 = p * dec * dec + leg.push_prob
    no_loss = p + leg.push_prob
    return e1, e2, no_loss, p


def _tilt(win_prob: float, tau: float, loadings: tuple[float, ...],
          fvec: tuple[float, ...]) -> float:
    """Move a branch's conditional win probability with the latent factor.

    Degenerate probabilities pass through untouched. That matters: a push
    branch has ``win_prob == 1`` by construction (the pushed legs return the
    stake no matter what), and pushing it through the logit would clamp it to
    ``1 - 1e-6`` and quietly bleed probability out of every ticket containing a
    whole-number leg.
    """
    if tau <= 0.0 or win_prob <= 0.0 or win_prob >= 1.0:
        return win_prob
    return _conditional_prob_multi(win_prob, tau, loadings, fvec)


def _unit_factors(
    unit: PricedUnit, loadings: tuple[float, ...], fvec: tuple[float, ...],
) -> tuple[float, float, float, float]:
    """``(E[return], E[return^2], P(no loss), P(clean sweep))`` for one unit.

    Conditional on the latent factor the units are independent, so the ticket's
    moments are the products of these — exactly, not approximately.
    """
    e1 = e2 = no_loss = all_win = 0.0
    for b in unit.branches:
        w = _tilt(b.win_prob, unit.tau, loadings, fvec)
        m = b.mass * w
        e1 += m * b.multiplier
        e2 += m * b.multiplier * b.multiplier
        no_loss += m
        if abs(b.multiplier - unit.decimal_odds) < 1e-12:
            all_win += m
    return e1, e2, no_loss, all_win


def price_units(units: list[PricedUnit], *, exact: bool = False) -> ParlayPricing:
    """Push-aware, correlation-aware price for a ticket of units.

    ``E[return] = E_F[ prod_u ( sum_b mass_b * w_b(F) * mult_b ) ]``.

    A unit is one game's worth of the ticket: a single leg, or a same-game
    bundle whose internal dependence has already been priced off the joint
    outcome distribution. This function only ever adds the *cross-game* layer.

    With ``exact=False`` (the default) the multi-channel correlation matrix is
    collapsed to rank 1 and integrated with a 15-node Gauss-Hermite rule — fast
    enough to price tens of thousands of candidate tickets during a slate
    search, and accurate to well under 1% of expected return.

    With ``exact=True`` every latent channel is integrated separately. This is
    two to three orders of magnitude slower and is meant for the handful of
    finalists that are actually going to be shown to a user, where the rank-1
    residual should not be allowed to move a borderline +EV verdict. The
    search ranks with the fast path and re-prices the shortlist with this one.
    """
    if not units:
        raise ValueError("a parlay needs at least one unit")
    assert_distinct_units(units)

    n = len(units)
    corr = correlation_matrix(units)
    b = rank1_loadings(corr)
    residual = rank1_residual(corr, b)

    gross = 1.0
    for u in units:
        gross *= u.decimal_odds

    if exact:
        # Per-unit loading vectors, integrated channel by channel.
        nodes = [
            (w0 * w1 * w2, (f0, f1, f2))
            for f0, w0 in _EXACT_NODES
            for f1, w1 in _EXACT_NODES
            for f2, w2 in _EXACT_NODES
        ]
        loadings: list[tuple[float, ...]] = [u.loadings for u in units]
    else:
        nodes = [(w, (f,)) for f, w in _GH_NODES]
        loadings = [(b[i],) for i in range(n)]

    e_return = 0.0
    e_return_sq = 0.0
    survive = 0.0
    all_win = 0.0
    # Second moment of the *conditional mean* return across the latent factor.
    # E[R | F] varies only because our parameters might be wrong, so its spread
    # is the standard error of our own EV estimate — distinct from return_sd,
    # which is the spread of the payout itself. The selection correction in
    # `shrinkage` needs the former, not the latter.
    e_cond_mean_sq = 0.0
    for w, fvec in nodes:
        prod_r = 1.0
        prod_r2 = 1.0
        prod_nl = 1.0
        prod_w = 1.0
        for i, unit in enumerate(units):
            e1, e2, no_loss, p_win = _unit_factors(unit, loadings[i], fvec)
            prod_r *= e1
            prod_r2 *= e2
            prod_nl *= no_loss
            prod_w *= p_win
        e_return += w * prod_r
        e_return_sq += w * prod_r2
        e_cond_mean_sq += w * prod_r * prod_r
        survive += w * prod_nl
        all_win += w * prod_w

    # --- audit baselines --------------------------------------------------- #
    zero = (0.0,) * len(nodes[0][1])
    indep_return = 1.0
    indep_all_win = 1.0
    no_push_return = 1.0
    for i, unit in enumerate(units):
        e1, _, _, p_win = _unit_factors(unit, loadings[i], zero)
        indep_return *= e1
        indep_all_win *= p_win
        # "pushes treated as losses" — what a win/lose engine would compute.
        no_push_return *= p_win * unit.decimal_odds

    var = max(0.0, e_return_sq - e_return * e_return)
    ev_var = max(0.0, e_cond_mean_sq - e_return * e_return)

    return ParlayPricing(
        n_legs=sum(u.n_legs for u in units),
        decimal_odds=gross,
        expected_return=e_return,
        expected_value=e_return - 1.0,
        survive_prob=survive,
        all_win_prob=all_win,
        return_sd=math.sqrt(var),
        ev_estimation_sd=math.sqrt(ev_var),
        independent_expected_return=indep_return,
        independent_all_win_prob=indep_all_win,
        correlation_effect=e_return - indep_return,
        push_effect=e_return - no_push_return,
        mean_pairwise_corr=mean_pairwise_correlation(corr),
        rank1_residual=residual,
        effective_loadings=b,
    )


def price_parlay(legs: list[PricedLeg], *, exact: bool = False) -> ParlayPricing:
    """Price a ticket of single legs. Thin wrapper over :func:`price_units`.

    Kept as the search path's entry point, and because every existing caller and
    test speaks in legs. One leg per game is still enforced here — the search
    does not build same-game tickets.
    """
    if not legs:
        raise ValueError("a parlay needs at least one leg")
    assert_distinct_games(legs)
    return price_units([to_unit(leg) for leg in legs], exact=exact)


def expected_return_exact(legs: list[PricedLeg]) -> float:
    """Convenience wrapper: ``price_parlay(legs, exact=True).expected_return``."""
    return price_parlay(legs, exact=True).expected_return
