"""Game-conditional outcome distribution: heteroskedastic sigma + joint (M, T).

What changed and why
--------------------
``prediction_dist`` models every game with the *same* margin sigma (16.5) and
the *same* total sigma, and treats margin and total as independent. Both
assumptions are wrong in the NFL in ways that cost real accuracy:

1. **Variance is not constant.** A 17-point mismatch and a 3-point division
   fight do not share a margin SD. Margin variance rises with the scoring
   environment (more possessions, more points per possession), with the size of
   the mismatch (blowouts are far more dispersed than close games), with pace,
   and with how boom-bust the two teams are. A flat sigma is simultaneously too
   wide on low-total toss-ups and far too narrow on high-total mismatches — it
   shows up as a U-shaped-in-the-middle, domed-at-the-tails PIT histogram and
   as systematically bad cover probabilities on big spreads.

2. **Margin and total are not independent.** When the favorite is blowing
   somebody out, the total runs high; when a game stays close, it stays low.
   Modelling them as two independent Normals makes it impossible to price a
   team total, an alternate line, or any same-game correlation correctly.

This module fixes both. The margin distribution is Normal with a
*game-conditional* sigma; the joint (margin, total) is a bivariate Normal whose
correlation is signed by which side is favored. From that one joint object,
every derived quantity — win, cover, over, team total, alt line, score grid —
falls out consistently.

A discrete margin PMF layer sits on top so key-number mass (3, 7, 10, 14) is
real rather than a lookup table bolted onto a continuous CDF.

Pure math + numpy. No DB, no param reads at import time.
"""
from __future__ import annotations

import math
from typing import Any

from . import prediction_dist as pd_

# ---- Import-safe defaults (live values resolve via param_registry at call time)

# Reference scoring environment: NFL games average ~45 combined points.
LEAGUE_TOTAL_ANCHOR = 45.0

# sigma_margin = base
#              * (E[total] / anchor) ** total_elasticity
#              * (1 + spread_k * min(|E[margin]|, spread_cap) / 14)
#              * pace_mult ** pace_elasticity
#              * variance_profile (explosiveness/havoc)
#              * (1 + rating_uncertainty_k * rating_sd_pts / 14)
MARGIN_SIGMA_BASE = 13.5
MARGIN_TOTAL_ELASTICITY = 0.35
MARGIN_SPREAD_K = 0.12
MARGIN_SPREAD_CAP = 35.0
MARGIN_PACE_ELASTICITY = 0.25
MARGIN_RATING_UNC_K = 0.30

# sigma_total scales closer to sqrt(points) — a points process, not a difference.
TOTAL_SIGMA_BASE = 10.0
TOTAL_TOTAL_ELASTICITY = 0.50
TOTAL_PACE_ELASTICITY = 0.45

# Wind. The only weather variable with a large, reliable effect on an NFL game.
# Above roughly 12 mph the deep passing game and the kicking game both degrade;
# offenses get more run-heavy and drives stall earlier. That *compresses* both
# distributions — a windy game is not a more random game, it is a lower-scoring
# and structurally tighter one, which is the opposite of the intuition most
# people (and most models) apply. Temperature and light precipitation are
# deliberately absent: their measured effect is small enough that including
# them mostly adds noise, and the public over-reacts to both.
#
# Effects are per-mph above the threshold, clamped — beyond ~30 mph the
# relationship stops being linear and the forecast stops being trustworthy.
WIND_THRESHOLD_MPH = 12.0
WIND_MARGIN_SIGMA_PER_MPH = 0.006
WIND_TOTAL_SIGMA_PER_MPH = 0.010
WIND_TOTAL_PTS_PER_MPH = 0.22
WIND_MAX_EFFECT_MPH = 30.0

# Correlation between margin and total, signed by the favorite. |rho| grows with
# the spread and saturates: a pick'em has no margin/total correlation, a 28-point
# favorite has a strong one (their blowouts are the high-scoring outcomes).
MT_RHO_MAX = 0.34
MT_RHO_SPREAD_SCALE = 17.0

# Hard bounds — a signal bug must never produce a degenerate distribution.
_SIGMA_M_BOUNDS = (9.0, 20.0)
_SIGMA_T_BOUNDS = (6.0, 16.0)

# Empirical NFL margin key-number *excess* mass, layered on the smoothed
# Normal then renormalized. Heavier than CFB at 3 and 7 — NFL games land on
# field goals and touchdowns more cleanly (fewer 2-point conversions, less
# variance). Values are the spike, not the total probability of that margin.
NFL_KEY_NUMBER_EXCESS: dict[int, float] = {
    3: 0.055, 7: 0.030, 6: 0.012, 10: 0.012, 4: 0.010,
    14: 0.008, 1: 0.008, 8: 0.006, 17: 0.003, 13: 0.003,
}


def _p(key: str, default: float) -> float:
    try:
        from . import param_registry
        return float(param_registry.value(key))
    except Exception:  # noqa: BLE001 — must run without a DB
        return default


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _wind_excess(wind_mph: float | None) -> float:
    """mph of wind above the threshold, capped. 0 when calm, indoors or unknown.

    An unknown forecast returns 0 rather than an assumed average: a game we have
    no wind reading for must price identically to the pre-weather model, not to
    a guess.
    """
    if not wind_mph or wind_mph <= 0:
        return 0.0
    thresh = _p("dist.wind_threshold_mph", WIND_THRESHOLD_MPH)
    return _clamp(float(wind_mph) - thresh, 0.0, WIND_MAX_EFFECT_MPH - thresh)


# ============================================================================
# Game-conditional sigma
# ============================================================================


def margin_sigma_base() -> float:
    """The reference sigma every conditional term scales. Public so callers with
    their own tuned sigma (the season simulator) can express theirs as a ratio
    against it rather than duplicating the parameter read."""
    return _p("dist.margin_sigma_base", MARGIN_SIGMA_BASE)


def margin_sigma_for_game(
    expected_margin: float,
    expected_total: float | None = None,
    *,
    pace_multiplier: float = 1.0,
    variance_multiplier: float = 1.0,
    context_sigma_mult: float = 1.0,
    rating_sd_pts: float = 0.0,
    wind_mph: float | None = None,
    indoor: bool = False,
    sample_scale: float = 1.0,
) -> float:
    """Margin SD conditioned on this specific game.

    Parameters
    ----------
    expected_margin
        Model expected margin (home perspective). Only its magnitude matters.
    expected_total
        Model expected combined points. Falls back to the league anchor.
    pace_multiplier
        The fundamentals layer's pace multiplier (>1 = faster than average).
    variance_multiplier
        Team style — boom-bust offenses widen the distribution.
    context_sigma_mult
        Product of the context layer's per-team sigma multipliers: a backup QB,
        a first-year coordinator, or an unreported injury situation all widen
        it. Kept separate from ``variance_multiplier`` on purpose — that one is
        a property of how the teams play, this one is a property of how much we
        know. Missing information should widen the distribution, not only shift
        its center.
    rating_sd_pts
        Uncertainty about the *rating gap* itself, in points. Early season and
        post-roster-churn this is large; it belongs in the game distribution
        rather than being smeared into a flat constant.
    wind_mph
        Forecast wind speed at kickoff. Compresses both distributions.
    indoor
        Dome or closed roof — wind is ignored entirely.
    sample_scale
        0-1 evidence weight. Thin evidence widens the distribution rather than
        pretending to a precision we do not have. Week 1 is not Week 12.
    """
    base = _p("dist.margin_sigma_base", MARGIN_SIGMA_BASE)
    a = _p("dist.margin_total_elasticity", MARGIN_TOTAL_ELASTICITY)
    k = _p("dist.margin_spread_k", MARGIN_SPREAD_K)
    pe = _p("dist.margin_pace_elasticity", MARGIN_PACE_ELASTICITY)
    ru = _p("dist.margin_rating_unc_k", MARGIN_RATING_UNC_K)
    anchor = _p("dist.league_total_anchor", LEAGUE_TOTAL_ANCHOR)

    sigma = base

    if expected_total and expected_total > 0 and anchor > 0:
        sigma *= (float(expected_total) / anchor) ** a

    spread_term = min(abs(float(expected_margin)), MARGIN_SPREAD_CAP)
    sigma *= 1.0 + k * (spread_term / 14.0)

    if pace_multiplier and pace_multiplier > 0:
        sigma *= float(pace_multiplier) ** pe

    sigma *= _clamp(float(variance_multiplier or 1.0), 0.85, 1.20)

    # Bounded independently of variance_multiplier so a runaway context
    # provider cannot blow up every probability on the board.
    sigma *= _clamp(float(context_sigma_mult or 1.0), 0.90, 1.35)

    if rating_sd_pts and rating_sd_pts > 0:
        sigma *= 1.0 + ru * (float(rating_sd_pts) / 14.0)

    if not indoor:
        w = _wind_excess(wind_mph)
        if w > 0:
            sigma *= 1.0 - _p("dist.wind_margin_sigma_per_mph",
                              WIND_MARGIN_SIGMA_PER_MPH) * w

    # Thin evidence: widen up to ~12% when we have almost nothing to go on.
    s = _clamp(float(sample_scale if sample_scale is not None else 1.0), 0.0, 1.0)
    sigma *= 1.0 + 0.12 * (1.0 - s)

    return _clamp(sigma, *_SIGMA_M_BOUNDS)


def total_sigma_for_game(
    expected_total: float | None = None,
    *,
    pace_multiplier: float = 1.0,
    variance_multiplier: float = 1.0,
    context_sigma_mult: float = 1.0,
    wind_mph: float | None = None,
    indoor: bool = False,
) -> float:
    """Total SD conditioned on the game's scoring environment, pace and wind."""
    base = _p("dist.total_sigma_base", TOTAL_SIGMA_BASE)
    a = _p("dist.total_total_elasticity", TOTAL_TOTAL_ELASTICITY)
    pe = _p("dist.total_pace_elasticity", TOTAL_PACE_ELASTICITY)
    anchor = _p("dist.league_total_anchor", LEAGUE_TOTAL_ANCHOR)

    sigma = base
    if expected_total and expected_total > 0 and anchor > 0:
        sigma *= (float(expected_total) / anchor) ** a
    if pace_multiplier and pace_multiplier > 0:
        sigma *= float(pace_multiplier) ** pe
    sigma *= _clamp(float(variance_multiplier or 1.0), 0.85, 1.20)
    sigma *= _clamp(float(context_sigma_mult or 1.0), 0.90, 1.35)

    if not indoor:
        w = _wind_excess(wind_mph)
        if w > 0:
            sigma *= 1.0 - _p("dist.wind_total_sigma_per_mph",
                              WIND_TOTAL_SIGMA_PER_MPH) * w

    return _clamp(sigma, *_SIGMA_T_BOUNDS)


def wind_total_points(wind_mph: float | None, indoor: bool = False) -> float:
    """Points to subtract from the expected total for wind. Never positive.

    Lives here rather than in the fundamentals layer because it shares the
    threshold with the variance terms above — one place to tune, one place to
    be wrong. This is the mean effect; the sigma terms are the spread effect,
    and wind moves both.
    """
    if indoor:
        return 0.0
    w = _wind_excess(wind_mph)
    if w <= 0:
        return 0.0
    return -_p("dist.wind_total_pts_per_mph", WIND_TOTAL_PTS_PER_MPH) * w


def margin_total_rho(expected_margin: float) -> float:
    """Correlation between final margin and final total.

    Signed by the favorite: the outcomes where a big favorite covers hugely are
    the same outcomes where the total goes over, so rho is positive when the
    home team is favored and negative when the away team is. A pick'em has
    essentially no correlation. ``tanh`` gives the saturation for free.
    """
    rho_max = _p("dist.margin_total_rho_max", MT_RHO_MAX)
    scale = _p("dist.margin_total_rho_scale", MT_RHO_SPREAD_SCALE)
    if scale <= 0:
        return 0.0
    return _clamp(rho_max * math.tanh(float(expected_margin) / scale), -0.95, 0.95)


# ============================================================================
# The joint distribution object
# ============================================================================


class GameDistribution:
    """Bivariate-Normal joint over (margin, total) for one game.

    Every probability the product quotes comes from this one object, so a team
    total, an alternate spread, a moneyline and an over can never disagree with
    each other. Home score H = (T + M) / 2 and away score A = (T - M) / 2 are
    linear in (M, T), so their marginals are Normal too and follow in closed
    form -- no simulation needed for the common cases.
    """

    __slots__ = ("meta", "mu_m", "mu_t", "rho", "sigma_m", "sigma_t")

    def __init__(
        self,
        expected_margin: float,
        expected_total: float,
        sigma_m: float,
        sigma_t: float,
        rho: float,
        meta: dict[str, Any] | None = None,
    ) -> None:
        self.mu_m = float(expected_margin)
        self.mu_t = float(expected_total)
        self.sigma_m = float(sigma_m)
        self.sigma_t = float(sigma_t)
        self.rho = _clamp(float(rho), -0.95, 0.95)
        self.meta = meta or {}

    # -- marginals ----------------------------------------------------------

    def win_prob(self) -> float:
        """P(home wins) = P(margin > 0)."""
        return pd_.norm_cdf(self.mu_m / self.sigma_m) if self.sigma_m > 0 else (
            1.0 if self.mu_m > 0 else 0.0
        )

    def cover_prob_home(self, home_line: float) -> float:
        """P(home covers ``home_line``) — sportsbook convention, negative = home favored."""
        if self.sigma_m <= 0:
            return 1.0 if self.mu_m > -home_line else 0.0
        return pd_.norm_cdf((self.mu_m + float(home_line)) / self.sigma_m)

    def over_prob(self, line: float) -> float:
        """P(combined points > ``line``)."""
        if self.sigma_t <= 0:
            return 1.0 if self.mu_t > line else 0.0
        return pd_.norm_cdf((self.mu_t - float(line)) / self.sigma_t)

    # -- team scores (this is what the independent model could not do) ------

    def team_score_params(self, home: bool = True) -> tuple[float, float]:
        """(mean, sd) of one team's final score.

        H = (T + M)/2 so Var(H) = (sigma_t^2 + sigma_m^2 + 2*rho*sigma_m*sigma_t)/4;
        A = (T - M)/2 flips the sign of the covariance term. With rho > 0 (home
        favored) the home side's score is the more dispersed one -- which is
        exactly right, and exactly what an independent model gets wrong.
        """
        sm, st, r = self.sigma_m, self.sigma_t, self.rho
        cov = 2.0 * r * sm * st
        var = (st * st + sm * sm + (cov if home else -cov)) / 4.0
        mean = (self.mu_t + self.mu_m) / 2.0 if home else (self.mu_t - self.mu_m) / 2.0
        return mean, math.sqrt(max(var, 1e-9))

    def team_total_over_prob(self, line: float, home: bool = True) -> float:
        """P(one team scores more than ``line``) — priced off the joint, not a guess."""
        mean, sd = self.team_score_params(home)
        if sd <= 0:
            return 1.0 if mean > line else 0.0
        return pd_.norm_cdf((mean - float(line)) / sd)

    def score_correlation(self) -> float:
        """Corr(home score, away score). Cov = (sigma_t^2 - sigma_m^2)/4."""
        _, sd_h = self.team_score_params(True)
        _, sd_a = self.team_score_params(False)
        cov = (self.sigma_t ** 2 - self.sigma_m ** 2) / 4.0
        denom = sd_h * sd_a
        return _clamp(cov / denom, -0.99, 0.99) if denom > 0 else 0.0

    # -- joint events (same-game correlation) -------------------------------

    def joint_prob(
        self,
        *,
        home_covers: float | None = None,
        over: float | None = None,
    ) -> float:
        """P(home covers a line AND the game goes over a total), jointly.

        This is the number a same-game parlay is actually worth. Multiplying the
        two marginals -- what an independent model forces you to do -- misprices
        it by several points whenever the spread is meaningful, because those
        two legs are correlated through the favorite's blowout scenarios.
        """
        if home_covers is None and over is None:
            return 1.0
        if over is None:
            return self.cover_prob_home(float(home_covers))
        if home_covers is None:
            return self.over_prob(float(over))
        # P(M > -line, T > total) on the standardized bivariate Normal.
        zm = (-float(home_covers) - self.mu_m) / self.sigma_m
        zt = (float(over) - self.mu_t) / self.sigma_t
        return _bivnor_upper(zm, zt, self.rho)

    # -- discrete layer (key numbers) ---------------------------------------

    def margin_pmf(self, lo: int = -70, hi: int = 70) -> dict[int, float]:
        """Discrete P(final margin == k) with NFL key-number mass.

        The continuous Normal is integrated over each integer bin, then the
        empirical key-number excess is layered on and the whole thing is
        renormalized. This is what makes push probability correct at *any*
        line rather than only at the handful in a lookup table.
        """
        pmf: dict[int, float] = {}
        for k in range(lo, hi + 1):
            z_hi = (k + 0.5 - self.mu_m) / self.sigma_m
            z_lo = (k - 0.5 - self.mu_m) / self.sigma_m
            pmf[k] = max(pd_.norm_cdf(z_hi) - pd_.norm_cdf(z_lo), 0.0)
        for k, excess in NFL_KEY_NUMBER_EXCESS.items():
            for signed in (k, -k):
                if signed in pmf:
                    pmf[signed] += excess
        z = sum(pmf.values())
        return {k: v / z for k, v in pmf.items()} if z > 0 else pmf

    def push_prob(self, home_line: float) -> float:
        """P(the spread pushes) — exact from the discrete PMF, 0 on a half-point."""
        if abs(home_line - round(home_line)) > 1e-9:
            return 0.0
        return self.margin_pmf().get(round(-home_line), 0.0)

    def cover_prob_discrete(self, home_line: float) -> tuple[float, float, float]:
        """(home cover, push, away cover) from the discrete PMF — sums to 1."""
        pmf = self.margin_pmf()
        need = -float(home_line)
        win = sum(p for k, p in pmf.items() if k > need + 1e-9)
        push = sum(p for k, p in pmf.items() if abs(k - need) < 1e-9)
        lose = sum(p for k, p in pmf.items() if k < need - 1e-9)
        return win, push, lose

    # -- intervals ----------------------------------------------------------

    def margin_interval(self, level: float = 0.80) -> tuple[float, float]:
        z = pd_.norm_ppf(0.5 + level / 2.0)
        return self.mu_m - z * self.sigma_m, self.mu_m + z * self.sigma_m

    def team_score_interval(self, home: bool = True, level: float = 0.80) -> tuple[float, float]:
        """Credible interval on one team's score, from the joint (not total ± margin)."""
        mean, sd = self.team_score_params(home)
        z = pd_.norm_ppf(0.5 + level / 2.0)
        return max(0.0, mean - z * sd), mean + z * sd

    # -- serialization ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        h_mean, h_sd = self.team_score_params(True)
        a_mean, a_sd = self.team_score_params(False)
        lo80, hi80 = self.margin_interval(0.80)
        lo50, hi50 = self.margin_interval(0.50)
        h_lo, h_hi = self.team_score_interval(True, 0.80)
        a_lo, a_hi = self.team_score_interval(False, 0.80)
        return {
            "expected_margin": round(self.mu_m, 1),
            "expected_total": round(self.mu_t, 1),
            "margin_sd": round(self.sigma_m, 2),
            "total_sd": round(self.sigma_t, 2),
            "margin_total_rho": round(self.rho, 3),
            "home_win_prob": round(self.win_prob(), 3),
            "margin_interval_50": [round(lo50, 1), round(hi50, 1)],
            "margin_interval_80": [round(lo80, 1), round(hi80, 1)],
            "home_score_mean": round(h_mean, 1),
            "home_score_sd": round(h_sd, 2),
            "away_score_mean": round(a_mean, 1),
            "away_score_sd": round(a_sd, 2),
            "home_score_range_80": [round(h_lo, 1), round(h_hi, 1)],
            "away_score_range_80": [round(a_lo, 1), round(a_hi, 1)],
            "score_correlation": round(self.score_correlation(), 3),
            "sigma_model": self.meta,
        }


def build_game_distribution(
    expected_margin: float,
    expected_total: float,
    *,
    pace_multiplier: float = 1.0,
    variance_multiplier: float = 1.0,
    context_sigma_mult: float = 1.0,
    rating_sd_pts: float = 0.0,
    wind_mph: float | None = None,
    indoor: bool = False,
    sample_scale: float = 1.0,
) -> GameDistribution:
    """Assemble the game-conditional joint distribution."""
    sm = margin_sigma_for_game(
        expected_margin, expected_total,
        pace_multiplier=pace_multiplier,
        variance_multiplier=variance_multiplier,
        context_sigma_mult=context_sigma_mult,
        rating_sd_pts=rating_sd_pts,
        wind_mph=wind_mph,
        indoor=indoor,
        sample_scale=sample_scale,
    )
    st = total_sigma_for_game(
        expected_total,
        pace_multiplier=pace_multiplier,
        variance_multiplier=variance_multiplier,
        context_sigma_mult=context_sigma_mult,
        wind_mph=wind_mph,
        indoor=indoor,
    )
    rho = margin_total_rho(expected_margin)
    meta = {
        "margin_sigma": round(sm, 2),
        "total_sigma": round(st, 2),
        "rho": round(rho, 3),
        "pace_multiplier": round(float(pace_multiplier or 1.0), 3),
        "variance_multiplier": round(float(variance_multiplier or 1.0), 3),
        "context_sigma_mult": round(float(context_sigma_mult or 1.0), 3),
        "rating_sd_pts": round(float(rating_sd_pts or 0.0), 2),
        "wind_mph": round(float(wind_mph), 1) if wind_mph else None,
        "indoor": bool(indoor),
        "sample_scale": round(float(sample_scale if sample_scale is not None else 1.0), 3),
        "flat_sigma_would_be": round(_p("dist.margin_sigma", pd_.NFL_MARGIN_SIGMA), 2),
    }
    return GameDistribution(expected_margin, expected_total, sm, st, rho, meta)


# ============================================================================
# Bivariate normal upper-orthant probability
# ============================================================================


def _bivnor_upper(zm: float, zt: float, rho: float) -> float:
    """P(Z1 > zm, Z2 > zt) for standard bivariate Normal with correlation rho.

    Drezner-Wesolowsky style Gauss-Legendre quadrature on the standard identity

        P(Z1 > h, Z2 > k) = Q(h)Q(k) + (1/2pi) * integral_0^rho
                            exp(-(h^2 - 2*t*h*k + k^2) / (2(1 - t^2))) / sqrt(1-t^2) dt

    20-point quadrature is accurate to ~1e-10 over |rho| <= 0.95, which is far
    more than a betting probability needs, and it avoids a scipy dependency in
    a module that has to stay importable in the worker.
    """
    r = _clamp(rho, -0.95, 0.95)
    base = (1.0 - pd_.norm_cdf(zm)) * (1.0 - pd_.norm_cdf(zt))
    if abs(r) < 1e-9:
        return _clamp(base, 0.0, 1.0)

    # 20-point Gauss-Legendre nodes/weights on [-1, 1].
    nodes = (
        -0.9931285991850949, -0.9639719272779138, -0.9122344282513259,
        -0.8391169718222188, -0.7463319064601508, -0.6360536807265150,
        -0.5108670019508271, -0.3737060887154195, -0.2277858511416451,
        -0.0765265211334973, 0.0765265211334973, 0.2277858511416451,
        0.3737060887154195, 0.5108670019508271, 0.6360536807265150,
        0.7463319064601508, 0.8391169718222188, 0.9122344282513259,
        0.9639719272779138, 0.9931285991850949,
    )
    weights = (
        0.0176140071391521, 0.0406014298003869, 0.0626720483341091,
        0.0832767415767048, 0.1019301198172404, 0.1181945319615184,
        0.1316886384491766, 0.1420961093183820, 0.1491729864726037,
        0.1527533871307258, 0.1527533871307258, 0.1491729864726037,
        0.1420961093183820, 0.1316886384491766, 0.1181945319615184,
        0.1019301198172404, 0.0832767415767048, 0.0626720483341091,
        0.0406014298003869, 0.0176140071391521,
    )
    half = r / 2.0
    acc = 0.0
    for x, w in zip(nodes, weights):
        t = half * (x + 1.0)
        one_minus = 1.0 - t * t
        if one_minus <= 1e-12:
            continue
        expo = -(zm * zm - 2.0 * t * zm * zt + zt * zt) / (2.0 * one_minus)
        acc += w * math.exp(expo) / math.sqrt(one_minus)
    integral = half * acc
    return _clamp(base + integral / (2.0 * math.pi), 0.0, 1.0)


# ============================================================================
# Fitting the sigma model against realized games
# ============================================================================


def _crps(mu: float, sigma: float, actual: float) -> float:
    return pd_.crps_normal(mu, sigma, actual)


def fit_sigma_model(
    rows: list[dict[str, Any]],
    *,
    max_iter: int = 60,
) -> dict[str, Any]:
    """Fit (base, total_elasticity, spread_k) by minimizing mean CRPS.

    ``rows`` are graded games: each needs ``expected_margin``, ``actual_margin``,
    and ideally ``expected_total`` / ``pace_multiplier``. Coordinate descent over
    a bounded grid — three parameters on a smooth, near-convex surface do not
    need an optimizer dependency, and this keeps the fit reproducible and
    auditable, which matters for a model whose calibration we publish.

    Returns the fitted parameters plus the CRPS of the fitted model vs the flat
    baseline, so a caller can refuse to promote a fit that does not actually
    beat the constant sigma it replaces.
    """
    usable = [
        r for r in rows
        if r.get("expected_margin") is not None and r.get("actual_margin") is not None
    ]
    if len(usable) < 150:
        return {"fitted": False, "reason": "insufficient_sample", "n": len(usable)}

    anchor = _p("dist.league_total_anchor", LEAGUE_TOTAL_ANCHOR)

    def _mean_crps(base: float, a: float, k: float) -> float:
        acc = 0.0
        for r in usable:
            em = float(r["expected_margin"])
            et = r.get("expected_total")
            sigma = base
            if et and float(et) > 0:
                sigma *= (float(et) / anchor) ** a
            sigma *= 1.0 + k * (min(abs(em), MARGIN_SPREAD_CAP) / 14.0)
            pace = r.get("pace_multiplier")
            if pace and float(pace) > 0:
                sigma *= float(pace) ** _p("dist.margin_pace_elasticity", MARGIN_PACE_ELASTICITY)
            sigma = _clamp(sigma, *_SIGMA_M_BOUNDS)
            acc += _crps(em, sigma, float(r["actual_margin"]))
        return acc / len(usable)

    # Flat baseline: the constant sigma this model replaces.
    flat = _p("dist.margin_sigma", pd_.NFL_MARGIN_SIGMA)
    baseline = sum(
        _crps(float(r["expected_margin"]), flat, float(r["actual_margin"])) for r in usable
    ) / len(usable)

    best = [
        _p("dist.margin_sigma_base", MARGIN_SIGMA_BASE),
        _p("dist.margin_total_elasticity", MARGIN_TOTAL_ELASTICITY),
        _p("dist.margin_spread_k", MARGIN_SPREAD_K),
    ]
    bounds = [(11.0, 20.0), (0.0, 1.0), (0.0, 0.45)]
    steps = [1.0, 0.10, 0.05]
    best_score = _mean_crps(*best)

    for _ in range(max_iter):
        improved = False
        for i in range(3):
            for direction in (1, -1):
                cand = list(best)
                cand[i] = _clamp(cand[i] + direction * steps[i], *bounds[i])
                if cand[i] == best[i]:
                    continue
                score = _mean_crps(*cand)
                if score < best_score - 1e-9:
                    best, best_score, improved = cand, score, True
        if not improved:
            steps = [s / 2.0 for s in steps]
            if all(s < 1e-3 for s in steps):
                break

    return {
        "fitted": True,
        "n": len(usable),
        "params": {
            "dist.margin_sigma_base": round(best[0], 3),
            "dist.margin_total_elasticity": round(best[1], 3),
            "dist.margin_spread_k": round(best[2], 3),
        },
        "crps_fitted": round(best_score, 4),
        "crps_flat_baseline": round(baseline, 4),
        "crps_improvement": round(baseline - best_score, 4),
        "crps_improvement_pct": round(
            100.0 * (baseline - best_score) / baseline, 2,
        ) if baseline > 0 else 0.0,
        "beats_flat": bool(best_score < baseline),
    }


def fit_margin_total_rho(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Estimate rho_max from realized (margin, total) residual pairs.

    Regresses the standardized total residual on ``tanh(expected_margin/scale)``
    times the standardized margin residual — i.e. fits the saturation curve the
    live model uses rather than a single global correlation, which would be
    near zero by construction once favorites on both sides are pooled.
    """
    pairs = [
        r for r in rows
        if all(r.get(k) is not None for k in
               ("expected_margin", "actual_margin", "expected_total", "actual_total"))
    ]
    if len(pairs) < 150:
        return {"fitted": False, "reason": "insufficient_sample", "n": len(pairs)}

    scale = _p("dist.margin_total_rho_scale", MT_RHO_SPREAD_SCALE)
    xs: list[float] = []
    ys: list[float] = []
    for r in pairs:
        em, et = float(r["expected_margin"]), float(r["expected_total"])
        sm = margin_sigma_for_game(em, et)
        st = total_sigma_for_game(et)
        zm = (float(r["actual_margin"]) - em) / sm
        zt = (float(r["actual_total"]) - et) / st
        s = math.tanh(em / scale) if scale > 0 else 0.0
        xs.append(s * zm)
        ys.append(zt)
    sxx = sum(x * x for x in xs)
    if sxx <= 0:
        return {"fitted": False, "reason": "degenerate", "n": len(pairs)}
    rho_max = sum(x * y for x, y in zip(xs, ys)) / sxx
    return {
        "fitted": True,
        "n": len(pairs),
        "params": {"dist.margin_total_rho_max": round(_clamp(rho_max, -0.6, 0.6), 3)},
    }


def fit_key_number_excess(
    margins: list[int],
    *,
    sigma: float | None = None,
) -> dict[int, float]:
    """Measure per-side key-number excess mass from realized margins.

    ``NFL_KEY_NUMBER_EXCESS`` is a considered guess, not a measurement. This
    computes the real thing from our own graded games: for each candidate key
    number, the observed frequency of that exact absolute margin minus what a
    smooth Normal of the same SD would predict, halved because ``margin_pmf``
    applies the excess at +k and -k.

    Returns only positive excesses — a key number that is not actually spiky in
    our sample earns no bonus rather than a negative correction, because a
    negative "excess" is far more likely to be sampling noise than a real hole
    in the margin distribution.
    """
    n = len(margins)
    if n < 500:
        return dict(NFL_KEY_NUMBER_EXCESS)

    sd = sigma or _p("dist.margin_sigma_base", MARGIN_SIGMA_BASE)
    mean = sum(margins) / n

    out: dict[int, float] = {}
    for k in sorted(NFL_KEY_NUMBER_EXCESS):
        observed = sum(1 for m in margins if abs(m) == k) / n
        expected = 0.0
        for signed in (k, -k):
            z_hi = (signed + 0.5 - mean) / sd
            z_lo = (signed - 0.5 - mean) / sd
            expected += pd_.norm_cdf(z_hi) - pd_.norm_cdf(z_lo)
        excess = (observed - expected) / 2.0
        if excess > 0:
            out[k] = round(excess, 4)
    return out
