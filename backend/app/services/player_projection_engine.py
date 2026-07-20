"""Player projection engine v2 — pure math, no DB, no network.

This is the player-level counterpart of ``prediction_dist``: every projected
stat is a *distribution*, not a point, and every probability the product shows
(P(over a prop line), anytime-TD, fantasy-point bands) is derived from that one
distribution so the numbers are mutually consistent — the same variance-first
contract the game model follows (see docs/PREDICTION_MODEL_SPEC.md).

Architecture (mirrors the hierarchical season Monte Carlo):

1. **Prior** — a multi-year, recency- and age-weighted per-game rate built from
   up to three prior seasons. Rookies get position-archetype priors upstream.
2. **Bayesian in-season update** — the prior acts as ``n0`` pseudo-games of
   evidence; each observed game shifts the posterior toward the player's actual
   current-season rate. Early weeks lean on the prior, late weeks on the data,
   with no tunable "switch week".
3. **Game coupling** — the posterior per-game rate is conditioned on the game
   model's outputs for a specific matchup: the team's expected points (scoring
   environment), the expected margin (game script → pass/rush tilt), and the
   opponent's positional defense factor. Weather/injury multipliers compose on
   top (owned by the orchestrating service).
4. **Two-component variance** — season aggregation separates *talent*
   uncertainty (how wrong our per-game mean might be — perfectly correlated
   across the remaining slate) from *game* noise (independent week to week).
   This is exactly the correlated latent-strength draw that fixed the season
   Monte Carlo's over-tight win bands (spec §2.1), applied to players:

       season_sd² = G² · talent_sd² + G · game_sd²

Pure Python (math only). The orchestration that reads weekly frames, the games
table and the Elo/game predictor lives in ``player_predictions_service``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from . import prediction_dist as dist

MODEL_VERSION = "player-proj-v2"

# ---- Prior construction -----------------------------------------------------

# Recency weights for prior seasons, most recent completed season first.
# Registry-backed ("player_engine" category): constants are import-safe
# fallbacks; _prior_weights()/_p() resolve live admin-tuned values.
PRIOR_SEASON_WEIGHTS: tuple[float, ...] = (1.0, 0.55, 0.30)


def _p(key: str) -> float:
    from . import param_registry
    return param_registry.value(key)


def _prior_weights() -> tuple[float, float, float]:
    return (_p("player.prior_w_s1"), _p("player.prior_w_s2"), _p("player.prior_w_s3"))

# How many pseudo-games of evidence the prior is worth, by stat class.
# Volume (attempts/targets/carries) is sticky role-driven → prior fades fast.
# Efficiency (yards) and scoring (TDs) are noisier → prior holds longer.
# Registry-backed defaults; _prior_n_for() resolves live admin values.
PRIOR_EFFECTIVE_GAMES: dict[str, float] = {
    "volume": 5.0,
    "yardage": 8.0,
    "scoring": 12.0,
}


def _prior_n_for(stat_class: str) -> float:
    key = {
        "volume": "player.prior_n_volume",
        "yardage": "player.prior_n_yardage",
        "scoring": "player.prior_n_scoring",
    }.get(stat_class, "player.prior_n_yardage")
    return _p(key)


def _shrink_k_for(stat_class: str) -> float:
    key = {
        "volume": "player.shrink_k_volume",
        "yardage": "player.shrink_k_yardage",
        "scoring": "player.shrink_k_scoring",
    }.get(stat_class, "player.shrink_k_yardage")
    return _p(key)

STAT_CLASS: dict[str, str] = {
    "attempts": "volume", "completions": "volume", "carries": "volume",
    "targets": "volume", "receptions": "volume",
    "passing_yards": "yardage", "rushing_yards": "yardage",
    "receiving_yards": "yardage", "fantasy_points_ppr": "yardage",
    "passing_tds": "scoring", "rushing_tds": "scoring",
    "receiving_tds": "scoring", "interceptions": "scoring",
}

# Stats that behave like (approximately) Poisson counts — used for anytime-TD.
TD_STATS = {"passing_tds", "rushing_tds", "receiving_tds"}

# Position-archetype per-game priors for players with no NFL history (rookies).
# Keyed by position → draft-capital tier ("day1", "day2", "day3"). Values are
# (mean, game_sd) per stat. Deliberately modest: rookie medians, not hype.
ROOKIE_ARCHETYPES: dict[str, dict[str, dict[str, tuple[float, float]]]] = {
    "QB": {
        "day1": {"attempts": (30.0, 7.0), "completions": (19.0, 5.0),
                 "passing_yards": (205.0, 65.0), "passing_tds": (1.1, 1.0),
                 "interceptions": (0.8, 0.9), "carries": (4.5, 2.5),
                 "rushing_yards": (20.0, 18.0), "rushing_tds": (0.15, 0.4),
                 "fantasy_points_ppr": (14.0, 7.0)},
        "day2": {"attempts": (26.0, 8.0), "completions": (16.0, 5.5),
                 "passing_yards": (170.0, 65.0), "passing_tds": (0.9, 0.9),
                 "interceptions": (0.8, 0.9), "carries": (3.5, 2.5),
                 "rushing_yards": (14.0, 15.0), "rushing_tds": (0.1, 0.3),
                 "fantasy_points_ppr": (11.0, 6.5)},
        "day3": {"attempts": (20.0, 9.0), "completions": (12.0, 6.0),
                 "passing_yards": (130.0, 60.0), "passing_tds": (0.6, 0.8),
                 "interceptions": (0.7, 0.8), "carries": (2.5, 2.0),
                 "rushing_yards": (9.0, 12.0), "rushing_tds": (0.05, 0.25),
                 "fantasy_points_ppr": (8.0, 6.0)},
    },
    "RB": {
        "day1": {"carries": (13.0, 5.0), "rushing_yards": (55.0, 30.0),
                 "rushing_tds": (0.40, 0.6), "targets": (3.2, 2.0),
                 "receptions": (2.5, 1.7), "receiving_yards": (18.0, 16.0),
                 "receiving_tds": (0.08, 0.28), "fantasy_points_ppr": (11.5, 6.0)},
        "day2": {"carries": (9.5, 5.0), "rushing_yards": (40.0, 26.0),
                 "rushing_tds": (0.28, 0.5), "targets": (2.4, 1.8),
                 "receptions": (1.9, 1.5), "receiving_yards": (13.0, 14.0),
                 "receiving_tds": (0.06, 0.24), "fantasy_points_ppr": (8.5, 5.5)},
        "day3": {"carries": (5.5, 4.5), "rushing_yards": (23.0, 22.0),
                 "rushing_tds": (0.15, 0.4), "targets": (1.4, 1.4),
                 "receptions": (1.1, 1.2), "receiving_yards": (8.0, 11.0),
                 "receiving_tds": (0.04, 0.2), "fantasy_points_ppr": (5.0, 4.5)},
    },
    "WR": {
        "day1": {"targets": (6.5, 2.8), "receptions": (4.2, 2.1),
                 "receiving_yards": (52.0, 32.0), "receiving_tds": (0.32, 0.55),
                 "fantasy_points_ppr": (11.0, 6.5)},
        "day2": {"targets": (4.8, 2.6), "receptions": (3.0, 1.9),
                 "receiving_yards": (37.0, 28.0), "receiving_tds": (0.22, 0.45),
                 "fantasy_points_ppr": (8.0, 5.5)},
        "day3": {"targets": (2.8, 2.2), "receptions": (1.8, 1.6),
                 "receiving_yards": (21.0, 22.0), "receiving_tds": (0.12, 0.35),
                 "fantasy_points_ppr": (4.8, 4.5)},
    },
    "TE": {
        "day1": {"targets": (4.6, 2.4), "receptions": (3.2, 1.8),
                 "receiving_yards": (34.0, 24.0), "receiving_tds": (0.25, 0.5),
                 "fantasy_points_ppr": (7.8, 5.0)},
        "day2": {"targets": (3.2, 2.2), "receptions": (2.2, 1.6),
                 "receiving_yards": (23.0, 20.0), "receiving_tds": (0.16, 0.4),
                 "fantasy_points_ppr": (5.5, 4.5)},
        "day3": {"targets": (1.8, 1.7), "receptions": (1.2, 1.2),
                 "receiving_yards": (12.0, 14.0), "receiving_tds": (0.08, 0.28),
                 "fantasy_points_ppr": (3.2, 3.5)},
    },
}


# ---- Role / depth-chart multipliers ------------------------------------------
#
# Per-game rates are availability-biased: a career backup QB's historical
# per-game numbers come only from games he actually started, so with no role
# adjustment Andy Dalton projects like a starter. The multiplier is the
# expected share of a starter's opportunity given current depth-chart slot.
# QB is winner-take-all; RB/WR rotate, so depth decays gently.

ROLE_MULTIPLIERS: dict[str, dict[int, float]] = {
    "QB": {1: 1.0, 2: 0.05, 3: 0.02},
    "RB": {1: 1.0, 2: 0.85, 3: 0.45, 4: 0.15},
    "WR": {1: 1.0, 2: 1.0, 3: 0.85, 4: 0.50, 5: 0.20},
    "TE": {1: 1.0, 2: 0.60, 3: 0.25},
}
_ROLE_FLOOR = {"QB": 0.02, "RB": 0.10, "WR": 0.12, "TE": 0.15}

# Leaderboards only list players projected for a meaningful role.
# Registry-backed: player.role_leaderboard_min (default 0.30).
ROLE_LEADERBOARD_MIN = 0.30


def role_leaderboard_min() -> float:
    return _p("player.role_leaderboard_min")


def role_multiplier(position: str, depth_order: int | None) -> float:
    """Expected opportunity share for a depth-chart slot (None = unknown → 1.0)."""
    if depth_order is None:
        return 1.0
    pos = (position or "").upper()
    table = ROLE_MULTIPLIERS.get(pos)
    if not table:
        return 1.0
    try:
        order = int(depth_order)
    except (TypeError, ValueError):
        return 1.0
    if order < 1:
        order = 1
    return table.get(order, _ROLE_FLOOR.get(pos, 0.15))


def scale_posterior(post: "StatPosterior", scale: float) -> "StatPosterior":
    """Scale a whole distribution (mean AND spreads) — used for role share.

    Unlike matchup multipliers (which shift the mean of a full-time player's
    game), a role share scales the player's entire opportunity, so the noise
    scales with it: a 5%-snap backup shouldn't carry a starter's ±60-yard band.
    """
    s = max(0.0, scale)
    return StatPosterior(
        mean=post.mean * s,
        game_sd=max(post.game_sd * s, 1e-6),
        talent_sd=post.talent_sd * s,
        prior_n=post.prior_n,
        obs_n=post.obs_n,
    )


def age_multiplier(position: str, age: int | None) -> float:
    """Aging-curve multiplier applied to the prior mean (not the variance).

    Piecewise-linear approximations of the well-documented positional curves:
    RBs decline earliest and fastest, WR/TE later, QBs latest and slowest.
    Young players get a modest growth bump. Clamped to [0.72, 1.10].
    """
    if age is None:
        return 1.0
    pos = (position or "").upper()
    peak_start, peak_end, decline = {
        "RB": (23, 26, 0.05),
        "WR": (24, 28, 0.035),
        "TE": (25, 29, 0.03),
        "QB": (26, 34, 0.02),
    }.get(pos, (24, 28, 0.03))

    if age < peak_start:
        mult = 1.0 - 0.03 * (peak_start - age)  # still ascending
    elif age <= peak_end:
        mult = 1.0
    else:
        mult = 1.0 - decline * (age - peak_end)
    return max(0.72, min(1.10, mult))


@dataclass(frozen=True)
class StatPosterior:
    """Everything downstream needs about one stat's per-game distribution."""
    mean: float          # posterior per-game mean (neutral matchup)
    game_sd: float       # week-to-week noise around the mean
    talent_sd: float     # our uncertainty about the mean itself
    prior_n: float       # pseudo-games the prior contributed
    obs_n: float         # observed current-season games


# Marcel-style regression: every prior is shrunk toward the positional
# starter-average rate by POSITION_SHRINK_K pseudo-games. Market consensus
# implicitly does this — without it our outliers (both directions) sit further
# from the market than the evidence justifies.
#
# Stat-class-specific: scoring rates (TDs, INTs) are the noisiest, most
# regression-prone stats in football — a two-season elite TD rate is mostly
# role + variance, and markets price heavy regression the flat K missed
# (the classic way a goal-line RB gets over-ranked). Volume is sticky and
# role-driven → lightest shrink.
# Registry-backed via _shrink_k_for(); constants remain import-safe defaults.
POSITION_SHRINK_K: dict[str, float] = {
    "volume": 2.0,
    "yardage": 3.0,
    "scoring": 6.0,
}


def build_prior(
    stat: str,
    seasons: list[dict],
    *,
    position: str = "",
    age: int | None = None,
    position_mean: float | None = None,
) -> tuple[float, float, float] | None:
    """Multi-year weighted prior → (mean, game_sd, prior_n).

    ``seasons`` is most-recent-first: [{"mean": .., "sd": .., "games": ..}, ..].
    Weights combine recency (PRIOR_SEASON_WEIGHTS) with games played, so a
    9-game injury season influences less than a 17-game one. The blended mean
    is aged with the positional curve, then regressed toward the positional
    starter mean (``position_mean``) by POSITION_SHRINK_K pseudo-games — light
    for a 3-season vet, heavy for a thin history.
    """
    pairs = [
        (w, s) for w, s in zip(_prior_weights(), seasons)
        if s and s.get("games", 0) > 0
    ]
    if not pairs:
        return None
    wsum = sum(w * min(float(s["games"]), 17.0) for w, s in pairs)
    if wsum <= 0:
        return None
    mean = sum(w * min(float(s["games"]), 17.0) * float(s["mean"]) for w, s in pairs) / wsum

    # Blend game-to-game SDs the same way; include cross-season disagreement so
    # a player whose role changed between seasons carries a wider prior.
    sd_within = sum(
        w * min(float(s["games"]), 17.0) * float(s.get("sd") or 0.0) for w, s in pairs
    ) / wsum
    if len(pairs) > 1:
        means = [float(s["mean"]) for _, s in pairs]
        mu = sum(means) / len(means)
        sd_between = math.sqrt(sum((m - mu) ** 2 for m in means) / len(means))
    else:
        sd_between = 0.0
    game_sd = math.sqrt(sd_within ** 2 + 0.5 * sd_between ** 2)

    mean *= age_multiplier(position, age)

    total_games = sum(min(float(s["games"]), 17.0) for _, s in pairs)
    # Regression to the positional starter mean (see POSITION_SHRINK_K note) —
    # scoring stats regress hardest, volume lightest. Live admin-tuned K.
    stat_class = STAT_CLASS.get(stat, "yardage")
    if position_mean is not None:
        k = _shrink_k_for(stat_class)
        mean = (total_games * mean + k * position_mean) / (total_games + k)

    n0 = _prior_n_for(stat_class)
    # A thin history (rookie year only, few games) is worth fewer pseudo-games.
    n0 *= min(1.0, total_games / 12.0)
    return mean, game_sd, max(1.0, n0)


def rookie_prior(stat: str, position: str, tier: str = "day2") -> tuple[float, float, float] | None:
    """Archetype prior for a player with no NFL history. Low confidence (n0=3)."""
    arch = ROOKIE_ARCHETYPES.get((position or "").upper(), {})
    kit = arch.get(tier) or arch.get("day2")
    if not kit or stat not in kit:
        return None
    mean, sd = kit[stat]
    return mean, sd, 3.0


def bayesian_update(
    prior_mean: float,
    prior_game_sd: float,
    prior_n: float,
    obs_mean: float | None,
    obs_game_sd: float | None,
    obs_n: float,
) -> StatPosterior:
    """Conjugate-style shrinkage: prior worth ``prior_n`` pseudo-games.

    posterior_mean = (n0·prior + n·observed) / (n0 + n)

    With 0 observed games the posterior IS the prior; as n grows the posterior
    converges on the player's actual current-season rate. ``talent_sd`` (our
    remaining uncertainty about the true mean) shrinks as 1/√(n0+n), which is
    what makes early-season season-long bands honest and late-season bands
    tight — the dynamic updating requested for the product.
    """
    n0 = max(0.5, prior_n)
    n = max(0.0, obs_n)
    if obs_mean is None or n <= 0:
        post_mean, post_sd = prior_mean, prior_game_sd
    else:
        post_mean = (n0 * prior_mean + n * obs_mean) / (n0 + n)
        o_sd = obs_game_sd if obs_game_sd is not None else prior_game_sd
        post_sd = math.sqrt((n0 * prior_game_sd ** 2 + n * o_sd ** 2) / (n0 + n))

    # Floor the game SD: even metronomic players have real week-to-week noise.
    post_sd = max(post_sd, 0.35 * math.sqrt(max(post_mean, 0.0)), 1e-6)
    talent_sd = post_sd / math.sqrt(n0 + n)
    return StatPosterior(
        mean=max(0.0, post_mean),
        game_sd=post_sd,
        talent_sd=talent_sd,
        prior_n=n0,
        obs_n=n,
    )


# ---- Game coupling ----------------------------------------------------------

# League avg is registry-backed via game.league_avg_points; constant is
# import-safe fallback for pure-math callers without a DB.
LEAGUE_AVG_TEAM_POINTS = 22.0

# Elasticities: how strongly each stat class responds to game environment.
# Registry-backed; constants are documentation / offline defaults.
_SCORING_ELASTICITY = {"volume": 0.30, "yardage": 0.55, "scoring": 1.0}
# Game-script tilt per expected point of margin (favored → run more, trail → pass).
_SCRIPT_PASS_PER_PT = -0.008
_SCRIPT_RUSH_PER_PT = 0.012
_SCRIPT_CAP = 0.12  # ±12%

_PASS_STATS = {"attempts", "completions", "passing_yards", "passing_tds", "interceptions"}
_RUSH_STATS = {"carries", "rushing_yards", "rushing_tds"}
_RECV_STATS = {"targets", "receptions", "receiving_yards", "receiving_tds"}


def _league_avg_points() -> float:
    try:
        return _p("game.league_avg_points")
    except Exception:  # noqa: BLE001 — pure-math fallback
        return LEAGUE_AVG_TEAM_POINTS


def game_environment_multiplier(
    stat: str,
    *,
    team_expected_pts: float,
    opp_expected_pts: float,
    defense_factor: float = 1.0,
) -> float:
    """Combine the game model's matchup outputs into one stat multiplier.

    - **Scoring environment**: team expected points vs league average, damped
      by the stat class's elasticity (TDs track points ~1:1, volume barely).
    - **Game script**: expected margin tilts pass vs rush volume.
    - **Positional defense**: opponent's factor vs this stat family (1.0 =
      league average; >1 = leaky). Computed upstream from weekly data.
    """
    env = 1.0
    league_avg = _league_avg_points()
    lo = _p("player.env_clamp_lo")
    hi = _p("player.env_clamp_hi")

    ratio = max(0.60, min(1.45, team_expected_pts / max(league_avg, 1e-6)))
    _elast_live = {
        "volume": _p("player.scoring_elasticity_volume"),
        "yardage": _p("player.scoring_elasticity_yardage"),
        "scoring": _p("player.scoring_elasticity_scoring"),
    }
    elast = _elast_live.get(STAT_CLASS.get(stat, "yardage"), 0.5)
    env *= ratio ** elast

    margin = team_expected_pts - opp_expected_pts
    if stat in _PASS_STATS or stat in _RECV_STATS:
        tilt = _p("player.script_pass_per_pt") * margin
    elif stat in _RUSH_STATS:
        tilt = _p("player.script_rush_per_pt") * margin
    else:
        tilt = 0.0
    script_cap = _p("player.script_cap")
    env *= 1.0 + max(-script_cap, min(script_cap, tilt))

    env *= max(lo, min(hi, defense_factor))
    # Overall clamp: no single game environment moves a projection by more
    # than the admin-tuned band. Uncapped, the three components compound into
    # swings the market never prices — keeping gaps to consensus honest.
    return max(lo, min(hi, env))


# ---- Distribution outputs ---------------------------------------------------


def stat_over_prob(mean: float, sd: float, line: float) -> float:
    """P(stat > line) for stat ~ Normal(mean, sd) truncated at 0.

    Player stats are non-negative; the truncation matters for small means
    (TDs, receptions for low-volume players) where a plain normal would put
    real mass below zero and skew prop probabilities.
    """
    if sd <= 0:
        return 1.0 if mean > line else 0.0
    if line < 0:
        return 1.0
    p_above_line = 1.0 - dist.norm_cdf((line - mean) / sd)
    p_above_zero = 1.0 - dist.norm_cdf((0.0 - mean) / sd)
    if p_above_zero <= 1e-12:
        return 0.0
    return min(1.0, p_above_line / p_above_zero)


def stat_interval(mean: float, sd: float, level: float = 0.8) -> tuple[float, float]:
    """Central credible interval, floored at 0."""
    z = dist.norm_ppf(0.5 + level / 2.0)
    return max(0.0, mean - z * sd), max(0.0, mean + z * sd)


def stat_quantile(mean: float, sd: float, q: float) -> float:
    return max(0.0, mean + dist.norm_ppf(q) * sd)


def anytime_td_prob(expected_tds: float) -> float:
    """P(≥1 TD) treating TDs as Poisson(λ = expected TDs)."""
    return 1.0 - math.exp(-max(0.0, expected_tds))


# ---- Market-implied means (price-aware prop anchoring) -----------------------
#
# A prop line alone says "the market's median is near here". The line PLUS the
# de-vigged over price says exactly where the market's mean sits relative to
# the line — books shade prices, not just numbers, so using the price recovers
# signal the line-only anchor threw away.


def market_implied_mean(line: float, over_prob: float | None, sd: float) -> float:
    """Market mean for a ~normal stat from (line, de-vigged P(over)).

    P(X > line) = p with X ~ N(m, sd)  ⇒  m = line + sd·Φ⁻¹(p).
    Falls back to the line itself when the price is missing or extreme (books
    occasionally post placeholder -10000 style prices; a clamped z would
    fabricate a huge shift). Shift capped at ±0.8·sd for the same reason.
    """
    if over_prob is None or not (0.08 <= over_prob <= 0.92):
        return line
    shift = sd * dist.norm_ppf(over_prob)
    try:
        cap_sd = _p("props.price_shift_cap_sd")
    except Exception:  # noqa: BLE001
        cap_sd = 0.8
    cap = cap_sd * sd
    return line + max(-cap, min(cap, shift))


def poisson_rate_from_over_prob(line: float, over_prob: float) -> float | None:
    """Invert P(X > line) = p for X ~ Poisson(λ) → market-implied rate λ.

    For the common 0.5 line this is the closed form λ = −ln(1−p). Higher lines
    (1.5, 2.5) are solved by bisection. This is what lets TD/INT props anchor
    the *rate* — the previous line-only anchor had to skip scoring stats
    because a 0.5 threshold is not a median.
    """
    if not (0.02 <= over_prob <= 0.98) or line < 0:
        return None
    k = int(math.floor(line))  # P(X > line) = P(X ≥ k+1)
    if k == 0:
        return -math.log(1.0 - over_prob)

    def p_over(lam: float) -> float:
        # 1 − CDF(k) for Poisson(λ); k is small (props stop at ~3.5).
        term, cdf = math.exp(-lam), math.exp(-lam)
        for i in range(1, k + 1):
            term *= lam / i
            cdf += term
        return 1.0 - cdf

    lo, hi = 1e-6, 12.0
    if p_over(hi) < over_prob:
        return None  # implied rate absurdly high — bad price, don't anchor
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if p_over(mid) < over_prob:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# ---- Durability / availability -----------------------------------------------
#
# A season projection is per-game rate × games — and "games" is not 17 for
# everyone. Drafters price durability (it's a big reason market ranks diverge
# from pure-rate models); we estimate it from the player's own games-played
# history, regressed toward the positional norm so one unlucky season doesn't
# brand a player injury-prone.

# Expected share of a full slate a healthy starter actually plays, by position.
# RBs miss the most time; QBs the least (of games they'd start).
# Registry-backed via _availability_norm(); constants are offline defaults.
AVAILABILITY_NORM: dict[str, float] = {"QB": 0.94, "RB": 0.87, "WR": 0.90, "TE": 0.90}
_AVAILABILITY_DEFAULT_NORM = 0.90
# Pseudo-games of league-norm evidence the prior contributes (≈ 1.2 seasons).
_AVAILABILITY_PSEUDO_GAMES = 20.0
_AVAILABILITY_FLOOR = 0.65


def _availability_norm(position: str) -> float:
    pos = (position or "").upper()
    key = {
        "QB": "player.avail_norm_qb",
        "RB": "player.avail_norm_rb",
        "WR": "player.avail_norm_wr",
        "TE": "player.avail_norm_te",
    }.get(pos)
    if key is None:
        return _AVAILABILITY_DEFAULT_NORM
    return _p(key)


def availability_rate(
    games_by_season: list[float | None], position: str,
) -> float:
    """Expected fraction of remaining games the player suits up for.

    ``games_by_season`` is most-recent-first games-played counts (aligned with
    PRIOR_SEASON_WEIGHTS). Recency-weighted games ÷ weighted 17-game exposure,
    shrunk toward the positional norm by _AVAILABILITY_PSEUDO_GAMES. A player
    with no history (rookie) gets exactly the norm.
    """
    norm = _availability_norm(position)
    exposure = 0.0
    played = 0.0
    for w, g in zip(_prior_weights(), games_by_season):
        if g is None:
            continue
        exposure += w * 17.0
        played += w * min(float(g), 17.0)
    pseudo = _p("player.availability_pseudo_games")
    rate = (played + pseudo * norm) / (exposure + pseudo)
    return max(_p("player.availability_floor"), min(1.0, rate))


# ---- Season aggregation -----------------------------------------------------


def aggregate_season(
    per_game_means: list[float],
    game_sd: float,
    talent_sd: float,
) -> dict[str, float]:
    """Sum per-game distributions into a season distribution.

    Talent error is perfectly correlated across games (if our per-game mean is
    2 yards high, it is high in every game) → contributes G²·talent_sd².
    Week-to-week noise is independent → contributes G·game_sd². Same
    hierarchical structure as the season Monte Carlo's latent-strength draw.
    """
    g = len(per_game_means)
    total = sum(per_game_means)
    if g == 0:
        return {"mean": 0.0, "sd": 0.0, "games": 0}
    sd = math.sqrt((g ** 2) * (talent_sd ** 2) + g * (game_sd ** 2))
    return {"mean": total, "sd": sd, "games": g}


SEASON_QUANTILES = (0.10, 0.25, 0.50, 0.75, 0.90)


def season_quantiles(mean: float, sd: float) -> dict[str, float]:
    return {f"p{int(q * 100)}": stat_quantile(mean, sd, q) for q in SEASON_QUANTILES}


# ---- Fantasy scoring ----------------------------------------------------------

SCORING_FORMATS = ("ppr", "half_ppr", "standard")


def fantasy_points(stats: dict[str, float], scoring: str = "ppr") -> float:
    """Fantasy points from component stats (yahoo/espn default scoring)."""
    rec_bonus = {"ppr": 1.0, "half_ppr": 0.5, "standard": 0.0}.get(scoring, 1.0)
    return (
        0.04 * stats.get("passing_yards", 0.0)
        + 4.0 * stats.get("passing_tds", 0.0)
        - 2.0 * stats.get("interceptions", 0.0)
        + 0.1 * stats.get("rushing_yards", 0.0)
        + 6.0 * stats.get("rushing_tds", 0.0)
        + 0.1 * stats.get("receiving_yards", 0.0)
        + 6.0 * stats.get("receiving_tds", 0.0)
        + rec_bonus * stats.get("receptions", 0.0)
    )


def fantasy_sd(stat_sds: dict[str, float], scoring: str = "ppr") -> float:
    """SD of fantasy points assuming independent component stats (conservative
    for same-player stats, which correlate positively — documented tradeoff)."""
    rec_bonus = {"ppr": 1.0, "half_ppr": 0.5, "standard": 0.0}.get(scoring, 1.0)
    weights = {
        "passing_yards": 0.04, "passing_tds": 4.0, "interceptions": 2.0,
        "rushing_yards": 0.1, "rushing_tds": 6.0,
        "receiving_yards": 0.1, "receiving_tds": 6.0, "receptions": rec_bonus,
    }
    var = sum((w * stat_sds.get(k, 0.0)) ** 2 for k, w in weights.items())
    return math.sqrt(var)
