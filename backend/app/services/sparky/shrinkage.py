"""Calibration, edge shrinkage, and the winner's-curse correction.

This module is the difference between a parlay tool that *reports* +EV and one
that *has* +EV. Three distinct problems, three distinct fixes, applied in this
order:

1. **Calibration.** A leg probability is only a probability if 70% picks win
   70% of the time. The app already calibrates ``predict_game``, but Sparky's
   leg probability is a *blend* of that with a de-vigged market number and then
   a signal adjustment, and nothing guarantees the blend inherits calibration.
   :func:`calibrate` applies a Platt map in logit space whose two coefficients
   are fit from settled history by :func:`fit_platt`.

2. **Edge shrinkage.** The market is the sharpest single estimator available.
   Our claimed edge over it is part real signal and part estimation noise, and
   the honest split is an empirical question with a number attached:

       lambda = Var(signal) / (Var(signal) + Var(noise))

   :func:`fit_edge_lambda` estimates it directly by maximum likelihood on
   settled picks — a one-parameter logistic fit with the market's log-odds as a
   fixed offset. ``lambda = 1`` means take the model at face value; ``lambda =
   0`` means our edge is entirely noise and we should just bet the market (i.e.
   not bet). Every leg's probability is then rebuilt as
   ``sigmoid(market_logit + lambda * edge)`` before it is allowed anywhere near
   a parlay. **This is applied per leg, before the search runs**, which is the
   statistically correct place: shrink first, then select.

3. **Winner's curse.** Even with shrunk legs, searching a slate and reporting
   the best ticket guarantees the reported EV is optimistic — the winner is
   disproportionately the ticket whose residual estimation error happened to
   point up. :func:`selection_penalty` charges for that, propagating the
   expected order statistics of the *leg* pool through to ticket EV.

   Two things about this correction are easy to get wrong and both were, in
   earlier drafts of this module. First, the relevant count is the number of
   **legs** in the pool, not the number of tickets enumerated: a 4-leg search
   over 24 legs produces 10,626 tickets, but they are built from 24 estimates
   and neighbouring tickets share three of four legs, so treating them as
   independent draws produces a haircut nothing could ever clear. Second, this
   is a *residual* correction sitting on top of step 2, which is already the
   winner's-curse correction in its purest form (signal variance over total
   variance) — so ``kappa`` belongs well below 1.0, and charging full freight
   double-counts. See :data:`DEFAULT_SELECTION_KAPPA`.

Pure module: no DB, no network, no third-party imports. Historical rows are
passed in as plain dicts by ``sparky_service``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .odds_math import clamp, inv_logit, logit

# --------------------------------------------------------------------------- #
# Defaults (import-safe fallbacks; live values come from the param registry via
# the service layer, per this codebase's "never read params at import time" rule)
# --------------------------------------------------------------------------- #

#: Fallback edge-shrinkage factor used before enough history exists to fit one.
#: 0.45 is deliberately pessimistic: published work on sports models that beat
#: closing lines consistently finds well under half of naive model edge
#: survives contact with the market, and a parlay compounds the error N times.
DEFAULT_EDGE_LAMBDA = 0.45

#: Minimum settled picks before a fitted lambda is trusted over the default.
MIN_ROWS_FOR_LAMBDA = 150

#: Minimum settled picks before a fitted Platt map is trusted over identity.
MIN_ROWS_FOR_PLATT = 200

#: Typical logit-scale spread of model-vs-market disagreement on an NFL game.
#: Feeds the residual-uncertainty formula in :func:`leg_tau`; measurable from
#: history via :func:`edge_dispersion`.
DEFAULT_EDGE_DISPERSION = 0.30

#: Logit-scale sd of the market's own mispricing. This is the floor on how well
#: any leg can be known: even a perfect model cannot be more certain than the
#: closing line is wrong. NFL books are the sharpest market in football;
#: this number is the floor, not a claim that we know the line better than
#: the market knows itself.
DEFAULT_MARKET_NOISE = 0.10

#: How much of the *residual* selection penalty to apply on top of per-leg
#: shrinkage. Deliberately well below 1.0: the shrinkage in step 2 is already
#: the winner's-curse correction (it is literally
#: signal-variance-over-total-variance), so charging a full order-statistic
#: haircut on top of it double-counts and makes the +EV gate mathematically
#: impossible to pass at any edge size. What is left is second-order — sampling
#: error in the fitted lambda, and heterogeneity inside the leg pool that a
#: single global lambda does not capture. The right value is an empirical
#: question that the parlay backtest answers: if realized parlay hit rate comes
#: in below predicted, raise this.
DEFAULT_SELECTION_KAPPA = 0.25


# --------------------------------------------------------------------------- #
# 1. Calibration
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PlattMap:
    """``p_calibrated = sigmoid(a + b * logit(p_raw))``. Identity is a=0, b=1."""

    a: float = 0.0
    b: float = 1.0
    n_rows: int = 0
    fitted: bool = False

    def __call__(self, p: float) -> float:
        return inv_logit(self.a + self.b * logit(p))

    def as_dict(self) -> dict:
        return {
            "a": round(self.a, 4),
            "b": round(self.b, 4),
            "n_rows": self.n_rows,
            "fitted": self.fitted,
        }


IDENTITY = PlattMap()


def calibrate(p: float, cal: PlattMap | None = None) -> float:
    return (cal or IDENTITY)(p)


def fit_platt(rows: list[dict], *, min_rows: int = MIN_ROWS_FOR_PLATT) -> PlattMap:
    """Fit ``a, b`` by Newton-Raphson on the binomial log-likelihood.

    ``rows`` need ``{"prob": float, "won": bool}``. ``b < 1`` means the model is
    overconfident (spread the probabilities back toward 0.5); ``b > 1`` means it
    is underconfident. ``a != 0`` means a systematic side bias.

    Returns the identity map when there is not enough history to fit one
    honestly — a two-parameter fit on 40 games is noise wearing a lab coat.
    """
    clean = [
        (logit(float(r["prob"])), 1.0 if r.get("won") else 0.0)
        for r in rows
        if r.get("prob") is not None and r.get("won") is not None
    ]
    if len(clean) < min_rows:
        return PlattMap(n_rows=len(clean), fitted=False)

    a, b = 0.0, 1.0
    for _ in range(60):
        g_a = g_b = 0.0
        h_aa = h_ab = h_bb = 0.0
        for x, y in clean:
            p = inv_logit(a + b * x)
            r = y - p
            w = max(p * (1.0 - p), 1e-9)
            g_a += r
            g_b += r * x
            h_aa += w
            h_ab += w * x
            h_bb += w * x * x
        det = h_aa * h_bb - h_ab * h_ab
        if abs(det) < 1e-12:
            break
        da = (h_bb * g_a - h_ab * g_b) / det
        db = (h_aa * g_b - h_ab * g_a) / det
        a += da
        b += db
        if abs(da) < 1e-9 and abs(db) < 1e-9:
            break

    # A fit that wants to inflate confidence, or to flip the sign of the model,
    # is far more likely to be an artifact of a short sample than a real
    # discovery. Clamp to a range that can only ever make us more careful.
    return PlattMap(
        a=clamp(a, -0.75, 0.75),
        b=clamp(b, 0.25, 1.10),
        n_rows=len(clean),
        fitted=True,
    )


# --------------------------------------------------------------------------- #
# 2. Edge shrinkage toward the market
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class EdgeShrink:
    """How much of our claimed edge over the market we are willing to believe."""

    lam: float = DEFAULT_EDGE_LAMBDA
    n_rows: int = 0
    fitted: bool = False
    #: Log-likelihood improvement per pick vs. simply betting the market.
    #: Negative means the model adds nothing and lambda should be near zero.
    ll_gain: float = 0.0

    def as_dict(self) -> dict:
        return {
            "lambda": round(self.lam, 4),
            "n_rows": self.n_rows,
            "fitted": self.fitted,
            "ll_gain_per_pick": round(self.ll_gain, 5),
        }


DEFAULT_SHRINK = EdgeShrink()


def shrink_toward_market(
    model_prob: float, market_prob: float | None, shrink: EdgeShrink | None = None,
) -> float:
    """Rebuild a leg probability as ``sigmoid(market_logit + lambda * edge)``.

    With no market number available the model prob is returned unchanged but
    the caller should treat that leg as unpriceable: an edge is defined
    *relative to* a price, and without one there is nothing to have an edge
    over. The old engine's habit of synthesizing a price from the model's own
    probability is precisely the bug this signature is shaped to prevent.
    """
    if market_prob is None:
        return model_prob
    lam = clamp((shrink or DEFAULT_SHRINK).lam, 0.0, 1.5)
    m = logit(market_prob)
    edge = logit(model_prob) - m
    return inv_logit(m + lam * edge)


def fit_edge_lambda(
    rows: list[dict], *, min_rows: int = MIN_ROWS_FOR_LAMBDA,
) -> EdgeShrink:
    """Maximum-likelihood estimate of how much model edge is real.

    ``rows`` need ``{"model_prob", "market_prob", "won"}``. We fit the single
    parameter ``lambda`` in

        P(win) = sigmoid( logit(market) + lambda * (logit(model) - logit(market)) )

    by Newton's method, holding the market's log-odds as a fixed offset. This
    is the cleanest possible statement of "does our model add information the
    closing market does not already have, and how much" — and unlike a
    correlation or a hit rate it produces a number that can be *used*.

    Interpretation of the result:
      - ``lambda ~ 1``: model edge is real at face value.
      - ``lambda ~ 0.4``: less than half of claimed edge survives; a 4-point
        model edge is worth about 1.6 points of real edge.
      - ``lambda <= 0``: the model is actively worse than the market on this
        sample. Clamped to 0, which collapses every leg onto the market price
        and makes the strict +EV gate reject essentially everything. That is
        the correct behaviour, not a bug.
    """
    clean = []
    for r in rows:
        mp, kp, won = r.get("model_prob"), r.get("market_prob"), r.get("won")
        if mp is None or kp is None or won is None:
            continue
        m = logit(float(kp))
        d = logit(float(mp)) - m
        clean.append((m, d, 1.0 if won else 0.0))

    if len(clean) < min_rows:
        return EdgeShrink(n_rows=len(clean), fitted=False)
    if all(abs(d) < 1e-9 for _, d, _ in clean):
        return EdgeShrink(lam=0.0, n_rows=len(clean), fitted=False)

    lam = 0.5
    for _ in range(80):
        g = 0.0
        h = 0.0
        for m, d, y in clean:
            p = inv_logit(m + lam * d)
            g += (y - p) * d
            h += max(p * (1.0 - p), 1e-9) * d * d
        if h < 1e-12:
            break
        step = g / h
        lam += step
        if abs(step) < 1e-10:
            break

    lam_c = clamp(lam, 0.0, 1.0)

    # Log-likelihood gain per pick vs. lambda = 0 (bet the market, i.e. no edge).
    ll_fit = ll_base = 0.0
    for m, d, y in clean:
        pf = clamp(inv_logit(m + lam_c * d), 1e-9, 1 - 1e-9)
        pb = clamp(inv_logit(m), 1e-9, 1 - 1e-9)
        ll_fit += y * math.log(pf) + (1 - y) * math.log(1 - pf)
        ll_base += y * math.log(pb) + (1 - y) * math.log(1 - pb)

    return EdgeShrink(
        lam=lam_c,
        n_rows=len(clean),
        fitted=True,
        ll_gain=(ll_fit - ll_base) / len(clean),
    )


# --------------------------------------------------------------------------- #
# 2b. Band-aware edge shrinkage
# --------------------------------------------------------------------------- #
#
# A single global lambda says "this fraction of our claimed edge is real" and
# applies it identically to a -2000 favourite and a +160 dog. That assumption is
# the reason chalk kept surfacing as an edge.
#
# It is wrong for two independent reasons, both structural:
#
# 1. **Where the model has resolution.** The Elo/EPA/market blend is estimated
#    almost entirely on competitive games. Out at p > 0.85 the things that
#    actually decide whether a 30-point favourite covers or wins by 41 -- starters
#    pulled at the half, garbage-time scoring, backdoor drives against a soft
#    shell -- are not in the feature set at all. The model's disagreement with
#    the market out there is therefore much more likely to be noise than its
#    disagreement in a pick'em.
#
# 2. **Where the de-vig is trustworthy.** Books load hold onto the longshot side.
#    ``devig_power`` corrects for that far better than proportional
#    normalization, but the correction is itself an estimate, and its residual
#    error grows exactly as the price gets extreme -- which is precisely where a
#    claimed 1-point edge lives.
#
# So lambda is fitted **per probability band** and, until a band has enough
# settled rows to speak for itself, it falls back to the global lambda times a
# conservative prior multiplier. The priors are stated here as priors, not
# smuggled in as results: the tail multiplier is low because we have not
# demonstrated tail edge, and it will rise on its own the moment settled history
# shows the model beating the closing line out there.

#: Fair-probability cut points defining the bands.
BAND_CUTS: tuple[float, float] = (0.35, 0.80)

BAND_DOG = "dog"
BAND_MID = "mid"
BAND_CHALK = "chalk"

#: Multipliers applied to the global lambda for a band with insufficient
#: history. 1.0 would be "assume the tail behaves like the middle", which is the
#: assumption this whole section exists to reject.
DEFAULT_BAND_PRIORS: dict[str, float] = {
    BAND_DOG: 0.65,
    BAND_MID: 1.00,
    BAND_CHALK: 0.35,
}

#: A band needs at least this many settled rows before its own fit is used.
MIN_ROWS_PER_BAND = 120


def band_for(fair_prob: float) -> str:
    """Which trust band a leg falls in, from its de-vigged market probability.

    Keyed on the *market's* number rather than the model's on purpose: the band
    is a statement about where on the price curve the bet sits, and the market
    is the better estimate of that. Using the model's own probability would let
    an overconfident model promote itself into a friendlier band.
    """
    lo, hi = BAND_CUTS
    if fair_prob < lo:
        return BAND_DOG
    if fair_prob >= hi:
        return BAND_CHALK
    return BAND_MID


@dataclass(frozen=True)
class BandedShrink:
    """Per-band edge trust, with the global fit as the fallback."""

    global_shrink: EdgeShrink
    bands: dict[str, EdgeShrink]
    priors: dict[str, float]

    def for_band(self, band: str) -> EdgeShrink:
        fitted = self.bands.get(band)
        if fitted is not None and fitted.fitted:
            return fitted
        mult = self.priors.get(band, 1.0)
        return EdgeShrink(
            lam=clamp(self.global_shrink.lam * mult, 0.0, 1.0),
            n_rows=fitted.n_rows if fitted else 0,
            fitted=False,
            ll_gain=0.0,
        )

    def for_prob(self, fair_prob: float) -> EdgeShrink:
        return self.for_band(band_for(fair_prob))

    def as_dict(self) -> dict:
        return {
            "global": self.global_shrink.as_dict(),
            "cuts": list(BAND_CUTS),
            "bands": {
                b: {
                    **self.for_band(b).as_dict(),
                    "prior_multiplier": round(self.priors.get(b, 1.0), 3),
                    "own_fit": (self.bands.get(b).as_dict() if self.bands.get(b) else None),
                }
                for b in (BAND_DOG, BAND_MID, BAND_CHALK)
            },
        }


def fit_edge_lambda_banded(
    rows: list[dict],
    *,
    priors: dict[str, float] | None = None,
    min_rows_band: int = MIN_ROWS_PER_BAND,
) -> BandedShrink:
    """Fit the global lambda and one lambda per probability band.

    ``rows`` need ``{"model_prob", "market_prob", "won"}`` -- the same shape
    :func:`fit_edge_lambda` takes. Band membership is decided by
    ``market_prob``, which is the de-vigged number the pick was made against.
    """
    global_fit = fit_edge_lambda(rows)
    buckets: dict[str, list[dict]] = {BAND_DOG: [], BAND_MID: [], BAND_CHALK: []}
    for r in rows:
        kp = r.get("market_prob")
        if kp is None:
            continue
        buckets[band_for(float(kp))].append(r)

    bands = {
        b: fit_edge_lambda(rs, min_rows=min_rows_band) for b, rs in buckets.items()
    }
    return BandedShrink(
        global_shrink=global_fit,
        bands=bands,
        priors=dict(priors or DEFAULT_BAND_PRIORS),
    )


def band_dispersion(
    rows: list[dict], *, default: float = DEFAULT_EDGE_DISPERSION,
) -> dict[str, float]:
    """Per-band logit-scale dispersion of model-vs-market disagreement."""
    buckets: dict[str, list[dict]] = {BAND_DOG: [], BAND_MID: [], BAND_CHALK: []}
    for r in rows:
        kp = r.get("market_prob")
        if kp is None:
            continue
        buckets[band_for(float(kp))].append(r)
    return {b: edge_dispersion(rs, default=default) for b, rs in buckets.items()}


def edge_dispersion(rows: list[dict], *, default: float = DEFAULT_EDGE_DISPERSION) -> float:
    """Logit-scale sd of ``logit(model) - logit(market)`` over settled picks."""
    ds = []
    for r in rows:
        mp, kp = r.get("model_prob"), r.get("market_prob")
        if mp is None or kp is None:
            continue
        ds.append(logit(float(mp)) - logit(float(kp)))
    if len(ds) < 30:
        return default
    mean = sum(ds) / len(ds)
    var = sum((d - mean) ** 2 for d in ds) / max(1, len(ds) - 1)
    return clamp(math.sqrt(var), 0.05, 1.20)


def leg_tau(
    shrink: EdgeShrink | None = None,
    *,
    dispersion: float = DEFAULT_EDGE_DISPERSION,
    market_noise: float = DEFAULT_MARKET_NOISE,
) -> float:
    """Residual logit-scale uncertainty of a *shrunk* leg probability.

    This is the ``tau`` the correlation model wants, and getting its magnitude
    right matters more than almost anything else in the engine: it drives both
    the correlation effect and the selection penalty.

    The key point is that ``tau`` is **not** the raw model's error — it is the
    error remaining *after* the leg has been shrunk onto the market. A shrunk
    estimate sits close to the closing line, and the closing line is close to
    the truth, so the residual is small:

        tau^2 = lambda^2 * (1 - lambda) * dispersion^2  +  market_noise^2
                \\_______ noise we did not shrink away ______/   \\__ floor __/

    Note the shape at the endpoints, which is the sanity check:
      - ``lambda -> 0`` (model adds nothing): we simply bet the market, so the
        only uncertainty left is the market's own — ``tau -> market_noise``.
      - ``lambda -> 1`` (model edge fully real): nothing was shrunk away, so
        again only market noise remains.
      - in between, the un-shrunk share of a noisy disagreement adds variance.

    An earlier version of this scaled ``tau`` *up* as lambda fell, on the
    reasoning that a weak model is a noisy model. That is backwards: a weak
    model gets pulled onto the market and therefore becomes *more* certain, not
    less. The mistake made every leg look far more uncertain than it is, which
    in turn made the selection penalty exceed the expected value of every
    possible ticket at every possible edge size.
    """
    lam = clamp((shrink or DEFAULT_SHRINK).lam, 0.0, 1.0)
    var = lam * lam * (1.0 - lam) * dispersion * dispersion + market_noise * market_noise
    return clamp(math.sqrt(var), 0.03, 0.60)


# --------------------------------------------------------------------------- #
# 3. Winner's curse / selection correction
# --------------------------------------------------------------------------- #


def expected_max_normal(k: int) -> float:
    """E[max of k iid standard normals], Blom's approximation.

    Exact for k = 1 (0.0) and accurate to ~0.5% from k = 2 upward, which is far
    inside the precision of anything it is multiplied by here.
    """
    if k <= 1:
        return 0.0
    if k == 2:
        return 1.0 / math.sqrt(math.pi)
    alpha = 0.375
    from_q = (k - alpha) / (k - 2.0 * alpha + 1.0)
    return _norm_ppf(from_q)


def _order_stat_z(n: int, i: int) -> float:
    """Expected value of the ``i``-th largest of ``n`` standard normals (Blom)."""
    if n <= 1:
        return 0.0
    i = max(1, min(i, n))
    alpha = 0.375
    q = (n - i + 1 - alpha) / (n - 2.0 * alpha + 1.0)
    return _norm_ppf(clamp(q, 1e-9, 1 - 1e-9))


def selection_z(n_leg_pool: int, n_legs: int, rank: int = 1) -> float:
    """How many standard errors of optimism a top-ranked ticket carries.

    The naive winner's-curse formula — expected max of K standard normals for
    K enumerated tickets — is badly wrong here, and wrong in the expensive
    direction. A 4-leg search over a 24-leg pool enumerates 10,626 tickets, but
    those tickets are built from only **24** underlying probability estimates,
    and any two neighbouring tickets share three of their four legs, so their
    errors are very nearly the same error. Treating them as 10,626 independent
    draws would produce a haircut around 3.9 standard errors and reject
    literally everything, forever.

    The search is really selecting the ``n_legs`` legs whose estimation noise
    points most favourably out of a pool of ``n_leg_pool``. So the optimism is
    the **mean of the top ``n_legs`` order statistics of ``n_leg_pool``**
    standard normals — typically 1 to 2 standard errors, and, correctly,
    growing only logarithmically as the slate gets bigger.

    Lower-ranked tickets are displaced down the order statistics by roughly one
    leg-slot per rank, so they carry proportionally less selection optimism.
    """
    pool = max(1, n_leg_pool)
    n = max(1, min(n_legs, pool))
    if pool <= n:
        return 0.0  # no selection happened — the whole pool is in the ticket
    offset = max(0, rank - 1)
    zs = [_order_stat_z(pool, i) for i in range(1 + offset, 1 + offset + n)]
    return max(0.0, sum(zs) / len(zs))


def selection_penalty(
    leg_probs: list[float],
    leg_decimals: list[float],
    leg_taus: list[float],
    leg_pushes: list[float] | None = None,
    *,
    n_leg_pool: int,
    rank: int = 1,
    kappa: float = DEFAULT_SELECTION_KAPPA,
) -> float:
    """Expected optimism in a selected ticket's EV, as a positive number to subtract.

    Propagated through the legs, which is where selection actually happens —
    the search picks *legs* that look good, not tickets. For
    ``E[R] = prod_i (p_i * dec_i + push_i)``:

        dE[R]/dp_i = dec_i * prod_{j != i} (p_j * dec_j + push_j)

    and the selection bias in leg ``i``'s probability is ``z_i * sigma_p_i``,
    with ``sigma_p_i = tau_i * p_i * (1 - p_i)`` by the delta method from the
    logit scale, and ``z_i`` the order statistics of the pool. Summing gives a
    first-order estimate of how much of the ticket's headline EV is selection
    artifact.

    Doing it in leg space rather than through the ticket's ``ev_estimation_sd``
    matters: under the rank-1 collapse that quantity captures only the *shared*
    factor, while selection operates mostly on the *idiosyncratic* noise that
    makes one leg look better than its neighbour.
    """
    n = len(leg_probs)
    if n == 0 or kappa <= 0:
        return 0.0
    pool = max(n, n_leg_pool)
    if pool <= n:
        # The whole pool went into the ticket, so nothing was selected and there
        # is no optimism to charge for. Mirrors the guard in `selection_z`;
        # without it a two-leg ticket built from a two-leg pool was penalized
        # for a choice it never made.
        return 0.0
    pushes = leg_pushes or [0.0] * n
    factors = [p * d + q for p, d, q in zip(leg_probs, leg_decimals, pushes)]

    offset = max(0, rank - 1)
    zs = [_order_stat_z(pool, i) for i in range(1 + offset, 1 + offset + n)]

    total = 0.0
    for i in range(n):
        others = 1.0
        for j in range(n):
            if j != i:
                others *= factors[j]
        d_ev = leg_decimals[i] * others
        sigma_p = leg_taus[i] * leg_probs[i] * (1.0 - leg_probs[i])
        total += d_ev * max(0.0, zs[i]) * sigma_p
    return max(0.0, kappa * total)


# --------------------------------------------------------------------------- #
# Small numerics (kept local so this package stays dependency-free)
# --------------------------------------------------------------------------- #


def _norm_ppf(p: float) -> float:
    """Acklam's inverse normal CDF — accurate to ~1e-9 over (0, 1)."""
    p = clamp(p, 1e-12, 1 - 1e-12)
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
