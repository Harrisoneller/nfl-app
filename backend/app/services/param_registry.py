"""Central model-parameter registry — every tunable, declared once, DB-tunable.

Why this exists
---------------
The projection stack is full of judgment-call constants: market blend weights,
Elo K-factor, scoring elasticities, prop anchor caps, ADP decay, clamps,
sigmas. They used to be module-level constants — changing one meant a code
edit and a redeploy, with no record of what changed or why. This registry
makes every one of them a first-class, admin-tunable, audited parameter:

* **Declared once.** Each tunable is a ``ParamSpec`` below: key, label, plain-
  English description, code default, hard bounds, category, and what it
  affects. The spec IS the documentation and the admin UI schema.
* **Resolved at call time.** Services call ``param_registry.value("elo.k_factor")``
  instead of reading a constant. Resolution order: preview overlay (context-
  local, used by the impact-preview endpoint) → DB override (``model_params``
  row) → code default. No DB row → exact pre-registry behavior.
* **Hot.** Values are cached in the process cache for ``_MAP_TTL`` seconds and
  the cache is version-bumped on every write, so a change takes effect within
  seconds on every replica — no restart, no redeploy.
* **Fail-open.** Any DB problem resolves to code defaults. The tuning layer
  must never take projections down.
* **Audited.** Every set / revert / preset action writes an
  ``admin_audit_log`` row (see audit_service).

Adding a tunable
----------------
Declare a ``ParamSpec`` in the appropriate ``_specs_*`` block, then read it
with ``value()`` at the point of use. Never read a registry-backed constant
at import time — module import happens before the DB exists.
"""
from __future__ import annotations

import contextlib
import hashlib
import math
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..cache import cache
from ..logging_config import get_logger

log = get_logger(__name__)

_MAP_KEY = "model_params:map"
_VERSION_KEY = "model_params:version"
_MAP_TTL = 15  # seconds; writes force-refresh immediately

# Preview overlay: {key: value} applied on top of everything for the current
# task/request only. Set via overlay() by the impact-preview endpoint.
_overlay: ContextVar[dict[str, float] | None] = ContextVar("param_overlay", default=None)


# ---- Spec ------------------------------------------------------------------


@dataclass(frozen=True)
class ParamSpec:
    key: str
    label: str
    description: str
    default: float
    min: float
    max: float
    category: str
    step: float = 0.01
    kind: str = "float"  # "float" | "int"
    unit: str = ""
    affects: tuple[str, ...] = field(default_factory=tuple)

    def clamp_valid(self, v: float) -> bool:
        return self.min <= v <= self.max and math.isfinite(v)

    def coerce(self, v: float) -> float:
        return float(round(v)) if self.kind == "int" else float(v)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "default": self.default,
            "min": self.min,
            "max": self.max,
            "step": self.step,
            "kind": self.kind,
            "unit": self.unit,
            "category": self.category,
            "affects": list(self.affects),
        }


CATEGORIES: dict[str, dict[str, str]] = {
    "elo": {
        "label": "Elo Ratings",
        "description": "Team power-rating engine. Feeds win probability, spreads, and every game headline.",
    },
    "game_model": {
        "label": "Game Scoring Model",
        "description": "How team aggregates become predicted scores, totals, and spreads.",
    },
    "epa_adjust": {
        "label": "Adjusted EPA Pipeline",
        "description": "Ridge opponent-adjustment of EPA/success rate. Pipeline params — take effect at the next aggregate materialization, not instantly.",
    },
    "market_blend": {
        "label": "Market Blend",
        "description": "How much the sportsbook/Kalshi consensus pulls the headline game numbers. model_only and edge are always preserved.",
    },
    "player_engine": {
        "label": "Player Projection Engine",
        "description": "Priors, shrinkage, game-script, environment clamps, and availability logic behind every player stat projection.",
    },
    "prop_anchors": {
        "label": "Prop-Line Anchoring",
        "description": "How much posted player prop lines pull weekly stat projections toward the market.",
    },
    "defense_adjust": {
        "label": "Opponent Defense Adjustment",
        "description": "Matchup multipliers from opponent defensive strength by stat family.",
    },
    "fantasy_market": {
        "label": "Fantasy Market (ADP)",
        "description": "How drafting-market consensus (ADP) blends into fantasy ranks, and how that trust decays in-season.",
    },
    "input_levers": {
        "label": "Input-Lever Mechanics",
        "description": "Elasticities and safety clamps governing how far admin team/player input levers can move the models.",
    },
    "distribution": {
        "label": "Outcome Distributions",
        "description": "Margin/total sigmas and key-number handling used for win prob, cover prob, and simulation.",
    },
    "weather": {
        "label": "Weather Adjustments",
        "description": "Wind/precip/cold thresholds and multipliers applied to weekly player projections outdoors.",
    },
    "injury": {
        "label": "Injury Status Multipliers",
        "description": "How Sleeper injury designations scale weekly player projections (OUT always zeros).",
    },
    "parlay": {
        "label": "Parlay Engine",
        "description": "How much of the model's claimed edge over the market is believed, how legs are assumed to move together, and how hard a ticket is penalized for having been chosen out of a slate rather than handed to us. `edge_lambda` is the single most consequential value here: it is the fraction of model-vs-market disagreement that history says is real signal rather than noise, it is fitted automatically once enough picks have settled, and setting it to 1.0 turns the strict +EV gate into a rubber stamp.",
    },
    "context": {
        "label": "Context Layer",
        "description": "What the historical model cannot see: who is actually playing, who is coaching, and the situation this specific game is played in. Every component is priced in points (a mean shift) and/or a sigma multiplier (a variance widening), stays separately visible in the explainability panel, and is capped so no single provider can dominate a number. `game_cap_pts` is the most consequential value here — it is the hard ceiling on how far all context combined may move a spread, and raising it is how a noisy injury feed turns into a bad line.",
    },
    "value": {
        "label": "Value Board",
        "description": "What makes a single bet worth placing, as opposed to a game worth predicting. These decide the two things the old confidence-ranked dashboard got wrong: heavy chalk is priced out rather than promoted (`ml_price_floor`), and a claimed edge has to survive both a minimum EV and a minimum confidence that the EV is real rather than estimation noise. `band_prior_chalk` is the most consequential value here — it is how much of the model's edge we believe on heavy favourites before that band has settled history of its own, and setting it to 1.0 puts 14-point favourites back at the top of the board.",
    },
}


def _spec(key: str, label: str, desc: str, default: float, lo: float, hi: float,
          cat: str, *, step: float = 0.01, kind: str = "float", unit: str = "",
          affects: tuple[str, ...] = ()) -> ParamSpec:
    return ParamSpec(key=key, label=label, description=desc, default=default,
                     min=lo, max=hi, category=cat, step=step, kind=kind,
                     unit=unit, affects=affects)


_GAME = ("game predictions", "spreads", "totals", "win prob")
_PARLAY = ("Sparky parlays", "parlay EV", "recommended tickets")
_VALUE = ("Sparky value board", "single-bet EV", "recommended stakes")
_PLAYER = ("player projections", "props", "start/sit", "fantasy")

_SPECS: tuple[ParamSpec, ...] = (
    # ---- Elo -----------------------------------------------------------------
    _spec("elo.k_factor", "K-factor",
          "Rating movement per game result. Higher reacts faster to recent results but is noisier.",
          20.0, 5.0, 50.0, "elo", step=1.0, affects=_GAME),
    _spec("elo.home_field_advantage", "Home-field advantage",
          "Elo points added to the home team (~25 Elo ≈ 1 spread point).",
          55.0, 0.0, 120.0, "elo", step=5.0, unit="Elo", affects=_GAME),
    _spec("elo.season_regression", "Season carry-over",
          "Fraction of last season's rating kept at new-season rollover; the rest regresses to 1500.",
          0.75, 0.0, 1.0, "elo", affects=_GAME),
    _spec("elo.elo_per_point", "Elo per spread point",
          "Conversion between Elo rating difference and spread points.",
          25.0, 10.0, 45.0, "elo", step=1.0, affects=_GAME),
    # ---- Game scoring model --------------------------------------------------
    _spec("game.league_avg_points", "League avg points/team",
          "Baseline points per team per game the scoring model regresses toward.",
          22.0, 17.0, 28.0, "game_model", step=0.1, unit="pts", affects=_GAME),
    _spec("game.rating_sigma_elo", "Rating uncertainty (Elo)",
          "Std-dev of true team strength in Elo points; controls how ratings translate to win prob confidence.",
          55.0, 20.0, 120.0, "game_model", step=1.0, unit="Elo", affects=_GAME),
    _spec("game.w_fundamentals", "Fundamentals weight (adj EPA)",
          "Share of the expected margin taken from the adjusted-EPA fundamentals layer; the rest stays on Elo. 0 = pure Elo.",
          0.40, 0.0, 1.0, "game_model", affects=_GAME),
    _spec("game.points_per_net_epa", "Points per net EPA/play",
          "Converts a net adjusted EPA/play edge into points per game (≈ effective plays with EPA-to-points damping).",
          50.0, 20.0, 90.0, "game_model", step=1.0, unit="pts", affects=_GAME),
    _spec("game.success_rate_weight", "Success-rate weight",
          "Share of fundamentals strength from adjusted success rate (stability) vs adjusted EPA (magnitude).",
          0.25, 0.0, 0.6, "game_model", affects=_GAME),
    _spec("game.cpoe_epa_per_pct", "CPOE credit (EPA/play per %)",
          "EPA/play added to offensive strength per point of CPOE — stabilizer for QB play beyond raw EPA.",
          0.004, 0.0, 0.02, "game_model", step=0.001, affects=_GAME),
    _spec("game.pace_elasticity", "Pace → total elasticity",
          "How strongly the matchup's neutral-situation pace (vs the league anchor) scales the predicted total.",
          0.5, 0.0, 1.0, "game_model", affects=_GAME),
    _spec("game.league_neutral_sec_per_play", "League neutral sec/snap",
          "League-average neutral-situation seconds per snap the pace multiplier is anchored to.",
          27.0, 22.0, 32.0, "game_model", step=0.1, unit="s", affects=_GAME),
    _spec("game.proe_total_pts", "PROE → total points",
          "Points added to the predicted total per point of combined PROE (pass-heavy offenses stop the clock, add plays).",
          0.08, 0.0, 0.30, "game_model", step=0.01, unit="pts", affects=_GAME),
    # ---- Adjusted-EPA pipeline (applies at next materialization) -------------
    _spec("epa.ridge_lambda", "Ridge shrinkage (pseudo-games)",
          "L2 shrinkage toward league average, in pseudo-games of evidence. Higher = steadier early season, slower to trust hot starts.",
          6.0, 0.0, 30.0, "epa_adjust", step=0.5, affects=_GAME),
    _spec("epa.prior_weight_games", "Prior-season weight (pseudo-games)",
          "Pseudo-games of last season's adjusted values blended in (prior regressed 50% to average). Fades as real games accrue.",
          3.0, 0.0, 10.0, "epa_adjust", step=0.5, affects=_GAME),
    # ---- Market blend --------------------------------------------------------
    _spec("market.w_base", "Base market weight",
          "Market share of the headline blend with a single source; grows per source up to the cap.",
          0.30, 0.0, 0.8, "market_blend", affects=_GAME),
    _spec("market.w_per_source", "Weight per source",
          "Additional market weight per independent book/exchange in the consensus.",
          0.10, 0.0, 0.3, "market_blend", affects=_GAME),
    _spec("market.w_cap", "Market weight cap",
          "Ceiling on market share of the blend — the model always keeps at least (1 − cap).",
          0.85, 0.3, 1.0, "market_blend", affects=_GAME),
    _spec("market.kalshi_book_equiv", "Kalshi book-equivalent",
          "How many sportsbooks one liquid Kalshi price counts as, in both consensus and weight.",
          2.0, 0.0, 5.0, "market_blend", step=0.5, affects=_GAME),
    _spec("market.line_lookback_hours", "Line staleness window",
          "Ignore persisted odds lines older than this many hours.",
          6.0, 1.0, 48.0, "market_blend", step=0.5, unit="h", affects=_GAME),
    # ---- Player engine -------------------------------------------------------
    _spec("player.prior_w_s1", "Prior weight: last season",
          "Weight of the most recent season in the player's historical prior.",
          1.0, 0.1, 1.0, "player_engine", affects=_PLAYER),
    _spec("player.prior_w_s2", "Prior weight: 2 seasons back",
          "Weight of the season before last in the prior.",
          0.55, 0.0, 1.0, "player_engine", affects=_PLAYER),
    _spec("player.prior_w_s3", "Prior weight: 3 seasons back",
          "Weight of the third season back in the prior.",
          0.30, 0.0, 1.0, "player_engine", affects=_PLAYER),
    _spec("player.script_pass_per_pt", "Game-script pass sensitivity",
          "Pass-volume multiplier change per point of expected margin (negative: favorites pass less).",
          -0.008, -0.03, 0.0, "player_engine", step=0.001, affects=_PLAYER),
    _spec("player.script_rush_per_pt", "Game-script rush sensitivity",
          "Rush-volume multiplier change per point of expected margin (favorites run more).",
          0.012, 0.0, 0.04, "player_engine", step=0.001, affects=_PLAYER),
    _spec("player.script_cap", "Game-script cap",
          "Maximum fractional volume shift game script can cause in either direction.",
          0.12, 0.0, 0.30, "player_engine", affects=_PLAYER),
    _spec("player.scoring_elasticity_volume", "Team-scoring → volume elasticity",
          "How much player volume stats scale with team scoring-level changes.",
          0.30, 0.0, 1.0, "player_engine", affects=_PLAYER),
    _spec("player.scoring_elasticity_yardage", "Team-scoring → yardage elasticity",
          "How much player yardage stats scale with team scoring-level changes.",
          0.55, 0.0, 1.2, "player_engine", affects=_PLAYER),
    _spec("player.availability_floor", "Availability floor",
          "Minimum games-played availability multiplier applied to season projections.",
          0.65, 0.3, 1.0, "player_engine", affects=_PLAYER),
    _spec("player.availability_pseudo_games", "Availability shrink (pseudo-games)",
          "Pseudo-observations pulling a player's availability toward the position norm; higher = trust history less.",
          20.0, 1.0, 60.0, "player_engine", step=1.0, affects=_PLAYER),
    _spec("player.prior_n_volume", "Prior strength: volume stats",
          "Pseudo-games of evidence the prior is worth for attempts/targets/carries (sticky role stats fade fast).",
          5.0, 1.0, 30.0, "player_engine", step=0.5, affects=_PLAYER),
    _spec("player.prior_n_yardage", "Prior strength: yardage stats",
          "Pseudo-games of prior evidence for yards stats (noisier than volume).",
          8.0, 1.0, 40.0, "player_engine", step=0.5, affects=_PLAYER),
    _spec("player.prior_n_scoring", "Prior strength: scoring stats",
          "Pseudo-games of prior evidence for TDs/INTs (noisiest class — prior holds longest).",
          12.0, 1.0, 50.0, "player_engine", step=0.5, affects=_PLAYER),
    _spec("player.shrink_k_volume", "Positional shrink K: volume",
          "Regression of volume rates toward positional starter mean (light — roles stick).",
          2.0, 0.0, 20.0, "player_engine", step=0.5, affects=_PLAYER),
    _spec("player.shrink_k_yardage", "Positional shrink K: yardage",
          "Regression of yardage rates toward positional starter mean.",
          3.0, 0.0, 25.0, "player_engine", step=0.5, affects=_PLAYER),
    _spec("player.shrink_k_scoring", "Positional shrink K: scoring",
          "Regression of TD/INT rates toward positional starter mean (heavy — markets price regression).",
          6.0, 0.0, 30.0, "player_engine", step=0.5, affects=_PLAYER),
    _spec("player.scoring_elasticity_scoring", "Team-scoring → TD elasticity",
          "How much player TD rates scale with team scoring-level changes (~1.0 tracks points 1:1).",
          1.0, 0.0, 1.5, "player_engine", affects=_PLAYER),
    _spec("player.env_clamp_lo", "Game-env multiplier floor",
          "Hardest a single game environment (scoring + script + defense) can suppress a stat.",
          0.75, 0.5, 1.0, "player_engine", affects=_PLAYER),
    _spec("player.env_clamp_hi", "Game-env multiplier ceiling",
          "Most a single game environment can inflate a stat.",
          1.30, 1.0, 1.8, "player_engine", affects=_PLAYER),
    _spec("player.role_leaderboard_min", "Role threshold for leaderboards",
          "Minimum depth-chart role multiplier to appear on season/weekly leaderboards.",
          0.30, 0.0, 1.0, "player_engine", affects=_PLAYER),
    _spec("player.avail_norm_qb", "Availability norm: QB",
          "Expected slate share a healthy starting QB plays (games-played durability baseline).",
          0.94, 0.5, 1.0, "player_engine", affects=_PLAYER),
    _spec("player.avail_norm_rb", "Availability norm: RB",
          "Expected slate share a healthy starting RB plays (RBs miss the most time).",
          0.87, 0.5, 1.0, "player_engine", affects=_PLAYER),
    _spec("player.avail_norm_wr", "Availability norm: WR",
          "Expected slate share a healthy starting WR plays.",
          0.90, 0.5, 1.0, "player_engine", affects=_PLAYER),
    _spec("player.avail_norm_te", "Availability norm: TE",
          "Expected slate share a healthy starting TE plays.",
          0.90, 0.5, 1.0, "player_engine", affects=_PLAYER),
    # ---- Prop anchors --------------------------------------------------------
    _spec("props.anchor_weight_per_book", "Anchor weight per book",
          "Pull toward the posted prop line per book quoting it.",
          0.12, 0.0, 0.4, "prop_anchors", affects=_PLAYER),
    _spec("props.anchor_weight_cap", "Anchor weight cap",
          "Maximum total pull toward prop lines for volume/yardage stats.",
          0.40, 0.0, 0.9, "prop_anchors", affects=_PLAYER),
    _spec("props.anchor_weight_cap_scoring", "Anchor cap (TD/scoring stats)",
          "Lower cap for scoring stats — TD prop lines are noisier than yardage lines.",
          0.30, 0.0, 0.9, "prop_anchors", affects=_PLAYER),
    _spec("props.price_shift_cap_sd", "Price-implied mean shift cap",
          "Max shift (in SDs) from line→market-implied mean when using de-vigged over price.",
          0.80, 0.0, 2.0, "prop_anchors", affects=_PLAYER),
    # ---- Defense adjustment --------------------------------------------------
    _spec("defense.shrink", "Defense factor shrink",
          "Regression of raw opponent-defense factors toward 1.0 (small weekly samples).",
          0.5, 0.0, 1.0, "defense_adjust", affects=_PLAYER),
    _spec("defense.clamp_lo", "Defense factor floor",
          "Hardest an elite defense can suppress a stat family.",
          0.80, 0.5, 1.0, "defense_adjust", affects=_PLAYER),
    _spec("defense.clamp_hi", "Defense factor ceiling",
          "Most a weak defense can inflate a stat family.",
          1.25, 1.0, 1.6, "defense_adjust", affects=_PLAYER),
    # ---- Fantasy market ------------------------------------------------------
    _spec("adp.weight_preseason", "ADP weight (preseason)",
          "Drafting-market share of preseason fantasy ranks before any games are played.",
          0.55, 0.0, 1.0, "fantasy_market", affects=("fantasy ranks", "VORP")),
    _spec("adp.weight_decay_per_week", "ADP decay per week",
          "How fast ADP trust fades as real-season data arrives.",
          0.045, 0.0, 0.2, "fantasy_market", step=0.005, affects=("fantasy ranks",)),
    _spec("adp.weight_floor", "ADP weight floor",
          "Minimum ADP influence kept all season (market memory never hits zero).",
          0.15, 0.0, 0.5, "fantasy_market", affects=("fantasy ranks",)),
    # ---- Input-lever mechanics ----------------------------------------------
    _spec("levers.pace_elasticity", "Pace → scoring elasticity",
          "Scoring response to a pace lever change (1.0 = proportional).",
          1.0, 0.0, 1.5, "input_levers", affects=_GAME + _PLAYER),
    _spec("levers.ypp_elasticity", "YPP → scoring elasticity",
          "Scoring response to a yards-per-play lever change (slightly damped by possession trade-off).",
          0.9, 0.0, 1.5, "input_levers", affects=_GAME + _PLAYER),
    _spec("levers.def_ypp_elasticity", "Def YPP → points-allowed elasticity",
          "How points-allowed responds to a defensive yards-per-play lever (defense-side twin of YPP).",
          0.85, 0.0, 1.5, "input_levers", affects=_GAME + _PLAYER),
    _spec("levers.team_ratio_clamp_lo", "Team lever clamp (floor)",
          "Lowest scoring ratio a single team lever can produce.",
          0.78, 0.5, 1.0, "input_levers", affects=_GAME),
    _spec("levers.team_ratio_clamp_hi", "Team lever clamp (ceiling)",
          "Highest scoring ratio a single team lever can produce.",
          1.25, 1.0, 1.6, "input_levers", affects=_GAME),
    _spec("levers.tilt_clamp_lo", "Pass-rate tilt clamp (floor)",
          "Lowest family-volume tilt from the pass-rate lever.",
          0.80, 0.5, 1.0, "input_levers", affects=_PLAYER),
    _spec("levers.tilt_clamp_hi", "Pass-rate tilt clamp (ceiling)",
          "Highest family-volume tilt from the pass-rate lever.",
          1.25, 1.0, 1.6, "input_levers", affects=_PLAYER),
    _spec("levers.share_ratio_clamp_lo", "Usage-share clamp (floor)",
          "Lowest ratio a player share lever (targets/rushes) can apply.",
          0.40, 0.1, 1.0, "input_levers", affects=_PLAYER),
    _spec("levers.share_ratio_clamp_hi", "Usage-share clamp (ceiling)",
          "Highest ratio a player share lever can apply.",
          1.75, 1.0, 3.0, "input_levers", step=0.05, affects=_PLAYER),
    _spec("levers.eff_ratio_clamp_lo", "Efficiency clamp (floor)",
          "Lowest ratio an efficiency lever (Y/T, Y/C) can apply.",
          0.70, 0.3, 1.0, "input_levers", affects=_PLAYER),
    _spec("levers.eff_ratio_clamp_hi", "Efficiency clamp (ceiling)",
          "Highest ratio an efficiency lever can apply.",
          1.40, 1.0, 2.0, "input_levers", affects=_PLAYER),
    _spec("levers.snap_ratio_clamp_lo", "Snap-rate clamp (floor)",
          "Lowest ratio the snap-rate lever can apply.",
          0.50, 0.1, 1.0, "input_levers", affects=_PLAYER),
    _spec("levers.snap_ratio_clamp_hi", "Snap-rate clamp (ceiling)",
          "Highest ratio the snap-rate lever can apply.",
          1.50, 1.0, 2.5, "input_levers", affects=_PLAYER),
    _spec("levers.eff_td_elasticity", "Efficiency → TD elasticity",
          "TD response to an efficiency lever change (yardage moves 1:1, TDs damped).",
          0.5, 0.0, 1.0, "input_levers", affects=_PLAYER),
    _spec("levers.availability_clamp_lo", "Availability lever floor",
          "Lowest games-played availability ratio the player availability lever can set.",
          0.40, 0.1, 1.0, "input_levers", affects=_PLAYER),
    _spec("levers.availability_clamp_hi", "Availability lever ceiling",
          "Highest games-played availability the player availability lever can set.",
          1.0, 0.5, 1.0, "input_levers", affects=_PLAYER),
    # ---- Distributions -------------------------------------------------------
    _spec("dist.margin_sigma", "Margin sigma",
          "Std-dev of NFL game margin around the spread. Drives win prob and cover prob.",
          13.5, 9.0, 18.0, "distribution", step=0.1, unit="pts", affects=_GAME),
    _spec("dist.total_sigma", "Total sigma",
          "Std-dev of game total around the predicted total. Drives over/under probabilities.",
          10.0, 6.0, 15.0, "distribution", step=0.1, unit="pts", affects=_GAME),
    _spec("dist.conditional_sigma_enabled", "Game-conditional sigma enabled",
          "1 = margin/total variance is conditioned on the specific game (scoring environment, mismatch size, pace, context, wind). 0 = the old flat sigma for every game. This changes every probability on the board, so it is a switch: if calibration regresses, revert here rather than deploying.",
          1.0, 0.0, 1.0, "distribution", step=1.0, kind="int", affects=_GAME + _PARLAY + _VALUE),
    # ---- Game-conditional distribution (dist_model) --------------------------
    # These were live in dist_model from the Sparky v2 port but never registered,
    # so the whole parlay/value pricing surface ran on hard-coded constants with
    # no admin visibility and no audit trail. Registering them changes nothing
    # numerically (defaults match the code) and makes the sigma model tunable.
    _spec("dist.margin_sigma_base", "Margin sigma base",
          "Reference margin std-dev before game-conditional scaling. Every other sigma term multiplies this.",
          13.5, 9.0, 18.0, "distribution", step=0.1, unit="pts", affects=_GAME + _PARLAY),
    _spec("dist.margin_total_elasticity", "Margin sigma / total elasticity",
          "How strongly margin variance scales with the scoring environment. 0 = flat sigma; higher = high-total games are treated as much more dispersed.",
          0.35, 0.0, 1.0, "distribution", affects=_GAME + _PARLAY),
    _spec("dist.margin_spread_k", "Margin sigma / spread term",
          "How much margin variance widens as the mismatch grows. Blowouts are far more dispersed than close games; 0 turns that off.",
          0.12, 0.0, 0.40, "distribution", affects=_GAME + _PARLAY),
    _spec("dist.margin_pace_elasticity", "Margin sigma / pace elasticity",
          "How strongly margin variance scales with neutral-situation pace (more plays = more variance).",
          0.25, 0.0, 1.0, "distribution", affects=_GAME + _PARLAY),
    _spec("dist.margin_rating_unc_k", "Margin sigma / rating uncertainty",
          "How much uncertainty about the rating gap itself widens the game distribution. Matters most early season and after roster churn.",
          0.30, 0.0, 1.0, "distribution", affects=_GAME + _PARLAY),
    _spec("dist.total_sigma_base", "Total sigma base",
          "Reference total std-dev before game-conditional scaling.",
          10.0, 6.0, 15.0, "distribution", step=0.1, unit="pts", affects=_GAME + _PARLAY),
    _spec("dist.total_total_elasticity", "Total sigma / total elasticity",
          "How strongly total variance scales with the scoring environment. ~0.5 treats scoring as a points process (sqrt scaling).",
          0.50, 0.0, 1.0, "distribution", affects=_GAME + _PARLAY),
    _spec("dist.total_pace_elasticity", "Total sigma / pace elasticity",
          "How strongly total variance scales with pace.",
          0.45, 0.0, 1.0, "distribution", affects=_GAME + _PARLAY),
    _spec("dist.league_total_anchor", "League total anchor",
          "Reference combined points a game is compared against when scaling both sigmas. Should track the league average total.",
          45.0, 35.0, 55.0, "distribution", step=0.5, unit="pts", affects=_GAME + _PARLAY),
    _spec("dist.margin_total_rho_max", "Margin/total correlation cap",
          "Maximum correlation between final margin and final total. Signed by the favorite; this is what makes same-game parlay pricing correct.",
          0.34, 0.0, 0.80, "distribution", affects=_GAME + _PARLAY),
    _spec("dist.margin_total_rho_scale", "Margin/total correlation scale",
          "Spread (pts) at which margin/total correlation reaches ~76% of its cap. Larger = correlation builds more slowly.",
          17.0, 5.0, 40.0, "distribution", step=0.5, unit="pts", affects=_GAME + _PARLAY),
    # ---- Wind in the game distribution ---------------------------------------
    # Distinct from the weather.* params below, which scale *player* projections.
    # These are the game-level mean and variance response, and they are the only
    # weather variable in the game model on purpose: wind has a large, reliable
    # effect, while temperature and light precipitation mostly add noise.
    _spec("dist.wind_threshold_mph", "Wind threshold (game model)",
          "Wind speed (mph) above which the game model starts cutting the total and compressing both distributions. Below this, wind is ignored.",
          12.0, 5.0, 25.0, "distribution", step=1.0, unit="mph", affects=_GAME + _PARLAY),
    _spec("dist.wind_total_pts_per_mph", "Wind total effect",
          "Points removed from the predicted total per mph of wind above the threshold. The public under-reacts to wind and over-reacts to cold; this prices only the former.",
          0.22, 0.0, 0.60, "distribution", unit="pts/mph", affects=_GAME + _PARLAY),
    _spec("dist.wind_margin_sigma_per_mph", "Wind margin-variance effect",
          "Fractional reduction in margin sigma per mph above the threshold. Wind makes a game lower-scoring and structurally tighter, not more random.",
          0.006, 0.0, 0.03, "distribution", step=0.001, affects=_GAME + _PARLAY),
    _spec("dist.wind_total_sigma_per_mph", "Wind total-variance effect",
          "Fractional reduction in total sigma per mph above the threshold.",
          0.010, 0.0, 0.04, "distribution", step=0.001, affects=_GAME + _PARLAY),
    # ---- Context layer -------------------------------------------------------
    _spec("context.enabled", "Context layer enabled",
          "Master switch. 0 disables every context component and predictions fall back to the pure historical model.",
          1.0, 0.0, 1.0, "context", step=1.0, kind="int", affects=_GAME + _PARLAY),
    _spec("context.game_cap_pts", "Context cap (points)",
          "Hard ceiling on how far all context components combined may move a game's expected margin. The single most important safety value in this category.",
          7.0, 0.0, 21.0, "context", step=0.5, unit="pts", affects=_GAME + _PARLAY),
    _spec("context.sigma_mult_cap", "Context sigma multiplier cap",
          "Ceiling on how much context may widen a game's margin distribution. 1.0 disables the variance channel entirely.",
          1.25, 1.0, 1.60, "context", affects=_GAME + _PARLAY),
    _spec("context.min_confidence", "Context minimum confidence",
          "Context below this confidence is dropped rather than applied at a discount. Keeping a fraction of a noise signal is how a model drifts.",
          0.25, 0.0, 1.0, "context", affects=_GAME + _PARLAY),
    _spec("context.staleness_hours", "Context staleness window",
          "How old the newest context snapshot may be before the layer is treated as unavailable for that team. Injury news goes stale fast.",
          72.0, 6.0, 336.0, "context", step=6.0, unit="hrs", affects=_GAME + _PARLAY),
    _spec("context.unreported_sigma_mult", "Unreported-team sigma widening",
          "Sigma multiplier applied to a team with no availability rows at all. A team we have no report for is unreported, not healthy — that uncertainty belongs in the distribution.",
          1.03, 1.0, 1.20, "context", step=0.01, affects=_GAME + _PARLAY),
    # ---- Context: player valuation -------------------------------------------
    _spec("context.redistribution", "Injury redistribution factor",
          "Fraction of an absent player's value-above-replacement that is actually lost. An injured WR1's targets go to WR2, not to a void — charging full value is the classic injury-model error and is why most public adjustments are roughly twice too large. Fit this against closing-line movement before trusting anything else in the availability layer.",
          0.55, 0.0, 1.0, "context", affects=_GAME + _PARLAY + _VALUE),
    _spec("context.replacement_percentile", "Replacement percentile",
          "Percentile of the position's own player pool that defines replacement level. Lower = a harsher baseline and larger injury effects.",
          25.0, 5.0, 50.0, "context", step=1.0, kind="int", affects=_GAME),
    _spec("context.value_min_opportunities", "Minimum opportunities to value",
          "Season targets+carries a skill player needs before we put a measured number on him. Below this he falls back to a positional prior — small samples produce absurd per-play rates.",
          20.0, 5.0, 100.0, "context", step=5.0, kind="int", affects=_GAME),
    _spec("context.availability_cap_pts", "Availability cap (points)",
          "Ceiling on the non-QB availability component for one team. A bad feed should cost a fraction of a point, not a game.",
          6.0, 0.0, 14.0, "context", step=0.5, unit="pts", affects=_GAME + _PARLAY),
    _spec("context.availability_sigma_per_pt", "Availability sigma per point",
          "How much each point of missing production widens the game distribution. Missing players make a team less predictable, not only worse.",
          0.012, 0.0, 0.05, "context", step=0.001, affects=_GAME + _PARLAY),
    # ---- Context: quarterback -------------------------------------------------
    _spec("context.qb_dropbacks_per_game", "QB dropbacks per game",
          "Dropbacks used to convert a quarterback's EPA-per-dropback edge into points per game.",
          35.0, 20.0, 50.0, "context", step=1.0, unit="dropbacks", affects=_GAME + _PARLAY),
    _spec("context.qb_prior_attempts", "QB shrinkage strength",
          "Pseudo-dropbacks of league-average prior mixed into every quarterback's rate. The most important regularizer in the model: a QB with 60 attempts is mostly noise, and that is exactly the sample you get right after a starter goes down.",
          220.0, 50.0, 600.0, "context", step=10.0, affects=_GAME + _PARLAY),
    _spec("context.qb_replacement_epa", "QB replacement EPA/dropback",
          "EPA per dropback of a replacement-level quarterback. Every QB's point value is measured against this line.",
          -0.12, -0.40, 0.10, "context", step=0.01, affects=_GAME + _PARLAY),
    _spec("context.qb_prior_season_weight", "QB prior-season weight",
          "How much last season's dropbacks count toward this season's quarterback rating. Keeps Week 2 from being priced off 40 attempts.",
          0.50, 0.0, 1.0, "context", affects=_GAME + _PARLAY),
    _spec("context.qb_starter_dropbacks", "QB established-starter volume",
          "Career dropbacks at which a quarterback is regressed toward the league mean rather than toward replacement level. Shrinking an unproven backup toward the starter-dominated league mean is what collapses the starter-to-backup swing toward zero — the whole reason he is the backup is that he is not league average.",
          600.0, 100.0, 2000.0, "context", step=50.0, affects=_GAME + _PARLAY),
    _spec("context.qb_max_swing_pts", "QB swing cap (points)",
          "Ceiling on starter-minus-backup. A backup quarterback is worth roughly 4-7 points; this bounds the tail, it is not the expected value.",
          9.0, 0.0, 16.0, "context", step=0.5, unit="pts", affects=_GAME + _PARLAY),
    _spec("context.qb_sigma_per_pt", "QB sigma per point",
          "How much each point of quarterback downgrade widens the game distribution. A backup making his first start is genuinely less predictable, not just worse.",
          0.02, 0.0, 0.08, "context", step=0.005, affects=_GAME + _PARLAY),
    # ---- Context: status -> play probability ---------------------------------
    # Priors, not measurements. `availability.fit_play_probability` measures the
    # real mapping from our own snapshots joined to realized snap counts; these
    # stay as the fallback until that has run on a real sample.
    _spec("context.play_prob_out", "Play prob: Out",
          "Probability a player designated Out appears. Should stay 0.",
          0.0, 0.0, 0.2, "context", affects=_GAME),
    _spec("context.play_prob_doubtful", "Play prob: Doubtful",
          "Probability a player designated Doubtful appears.",
          0.06, 0.0, 0.5, "context", affects=_GAME),
    _spec("context.play_prob_questionable", "Play prob: Questionable",
          "Probability a player designated Questionable appears. The league-wide rate has drifted well above the folk number, and the practice-participation trajectory adjusts it further per player.",
          0.72, 0.2, 1.0, "context", affects=_GAME),
    _spec("context.play_prob_ir", "Play prob: IR",
          "Probability a player on injured reserve appears.",
          0.0, 0.0, 0.2, "context", affects=_GAME),
    _spec("context.play_prob_pup", "Play prob: PUP",
          "Probability a player on the physically-unable-to-perform list appears.",
          0.0, 0.0, 0.2, "context", affects=_GAME),
    _spec("context.play_prob_nfi", "Play prob: NFI",
          "Probability a player on the non-football-injury list appears.",
          0.0, 0.0, 0.2, "context", affects=_GAME),
    _spec("context.play_prob_suspended", "Play prob: Suspended",
          "Probability a suspended player appears.",
          0.0, 0.0, 0.2, "context", affects=_GAME),
    _spec("context.play_prob_available", "Play prob: Available",
          "Probability a player listed but not designated appears.",
          1.0, 0.5, 1.0, "context", affects=_GAME),
    _spec("context.play_prob_unknown", "Play prob: Unknown",
          "Probability a player with an unrecognized designation appears. High on purpose — an unparsed status is our problem, not evidence he is hurt.",
          0.90, 0.5, 1.0, "context", affects=_GAME),
    # ---- Weather -------------------------------------------------------------
    _spec("weather.wind_mod_mph", "Moderate wind threshold",
          "Wind speed (mph) at which moderate outdoor pass/recv penalties kick in.",
          15.0, 5.0, 30.0, "weather", step=1.0, unit="mph", kind="int", affects=_PLAYER),
    _spec("weather.wind_high_mph", "High wind threshold",
          "Wind speed (mph) at which heavy outdoor pass/recv penalties kick in.",
          25.0, 10.0, 45.0, "weather", step=1.0, unit="mph", kind="int", affects=_PLAYER),
    _spec("weather.pass_wind_mod_mult", "Pass mult @ moderate wind",
          "Passing-stat multiplier when wind ≥ moderate threshold.",
          0.92, 0.7, 1.0, "weather", affects=_PLAYER),
    _spec("weather.pass_wind_high_mult", "Pass mult @ high wind",
          "Passing-stat multiplier when wind ≥ high threshold.",
          0.85, 0.6, 1.0, "weather", affects=_PLAYER),
    _spec("weather.recv_wind_mod_mult", "Recv mult @ moderate wind",
          "Receiving-stat multiplier when wind ≥ moderate threshold.",
          0.94, 0.7, 1.0, "weather", affects=_PLAYER),
    _spec("weather.recv_wind_high_mult", "Recv mult @ high wind",
          "Receiving-stat multiplier when wind ≥ high threshold.",
          0.88, 0.6, 1.0, "weather", affects=_PLAYER),
    _spec("weather.precip_mod_in", "Moderate precip threshold",
          "Precipitation (inches) for moderate outdoor penalties.",
          0.15, 0.0, 1.0, "weather", step=0.05, unit="in", affects=_PLAYER),
    _spec("weather.precip_high_in", "Heavy precip threshold",
          "Precipitation (inches) for heavy outdoor penalties / rush boost.",
          0.40, 0.05, 2.0, "weather", step=0.05, unit="in", affects=_PLAYER),
    _spec("weather.pass_precip_mod_mult", "Pass mult @ moderate precip",
          "Passing-stat multiplier when precip ≥ moderate threshold.",
          0.93, 0.7, 1.0, "weather", affects=_PLAYER),
    _spec("weather.pass_precip_high_mult", "Pass mult @ heavy precip",
          "Passing-stat multiplier when precip ≥ heavy threshold.",
          0.85, 0.6, 1.0, "weather", affects=_PLAYER),
    _spec("weather.recv_precip_mod_mult", "Recv mult @ moderate precip",
          "Receiving-stat multiplier when precip ≥ moderate threshold.",
          0.95, 0.7, 1.0, "weather", affects=_PLAYER),
    _spec("weather.recv_precip_high_mult", "Recv mult @ heavy precip",
          "Receiving-stat multiplier when precip ≥ heavy threshold.",
          0.88, 0.6, 1.0, "weather", affects=_PLAYER),
    _spec("weather.rush_boost_mult", "Rush boost (wind/rain)",
          "Rushing-stat multiplier when wind ≥ 20 mph or precip ≥ heavy threshold.",
          1.04, 1.0, 1.25, "weather", affects=_PLAYER),
    _spec("weather.cold_temp_f", "Cold temperature threshold",
          "Temperature (°F) at or below which a light pass penalty applies.",
          25.0, 0.0, 45.0, "weather", step=1.0, unit="°F", kind="int", affects=_PLAYER),
    _spec("weather.cold_pass_mult", "Pass mult in cold",
          "Passing-stat multiplier when temperature ≤ cold threshold.",
          0.95, 0.8, 1.0, "weather", affects=_PLAYER),
    # ---- Injury --------------------------------------------------------------
    _spec("injury.doubtful_mult", "Doubtful multiplier",
          "Weekly projection scale for players designated Doubtful (OUT/IR always 0).",
          0.30, 0.0, 1.0, "injury", affects=_PLAYER),
    _spec("injury.questionable_mult", "Questionable multiplier",
          "Weekly projection scale for players designated Questionable.",
          0.85, 0.0, 1.0, "injury", affects=_PLAYER),

    # --- parlay engine -------------------------------------------------- #
    _spec("parlay.edge_lambda", "Edge trust (lambda)",
          "Fraction of the model's edge over the de-vigged market that is treated as "
          "real. Used only until enough settled picks exist to fit it by maximum "
          "likelihood, after which the fitted value wins. 1.0 means take the model at "
          "face value; 0.0 means the model adds nothing to the closing line and no bet "
          "should ever qualify. Below 0.5 is the historically defensible range for a "
          "model competing with a sharp NFL market.",
          0.45, 0.0, 1.0, "parlay", step=0.01, affects=_PARLAY),
    _spec("parlay.selection_kappa", "Selection penalty weight",
          "How much of the residual winner's-curse correction to charge. The per-leg "
          "shrinkage above is the primary correction; this covers what it misses. "
          "Charging a full order-statistic haircut on top (kappa = 1) double-counts and "
          "makes the +EV gate impossible to pass at any edge size. Raise it if the "
          "backtest shows realized parlay hit rate coming in under predicted.",
          0.25, 0.0, 1.0, "parlay", step=0.05, affects=_PARLAY),
    _spec("parlay.market_noise", "Market mispricing sd (logit)",
          "How wrong the closing line itself is, on the log-odds scale. This is the "
          "floor on how well any leg can be known — no model can be more certain than "
          "the market is wrong. NFL books are the sharpest in football; keep this "
          "tighter than a college-football setting would.",
          0.10, 0.01, 0.40, "parlay", step=0.01, affects=_PARLAY),
    _spec("parlay.rho_model", "Leg correlation: model error",
          "How strongly legs share exposure to 'are our numbers right today'. Every leg "
          "loads on this channel, which is what makes a parlay one correlated bet on the "
          "model rather than N independent bets. Setting it to 0 reproduces the old "
          "independence assumption exactly.",
          0.55, 0.0, 0.95, "parlay", step=0.05, affects=_PARLAY),
    _spec("parlay.rho_favorite", "Leg correlation: favourite regime",
          "Shared exposure of moneyline and spread legs to whether chalk holds on this "
          "slate. Signed by which side the leg takes.",
          0.20, 0.0, 0.90, "parlay", step=0.05, affects=_PARLAY),
    _spec("parlay.rho_scoring", "Leg correlation: scoring environment",
          "Shared exposure of total legs to whether the slate runs high or low — weather "
          "systems, officiating, rule regime. Signed by over/under, so stacking overs is "
          "priced as the correlated bet it is.",
          0.30, 0.0, 0.90, "parlay", step=0.05, affects=_PARLAY),
    _spec("parlay.min_leg_edge", "Minimum leg edge",
          "A leg must beat its own fair price by at least this much to enter the search "
          "pool. For cross-game tickets there is no such thing as a leg that is bad "
          "alone but good in a parlay, so every leg carries its own weight. The "
          "exception is genuine same-game correlation, which the engine now prices "
          "properly on the joint outcome distribution — and this filter is deliberately "
          "not applied to hand-built tickets, where the user named the legs.",
          0.01, 0.0, 0.10, "parlay", step=0.005, affects=_PARLAY),
    _spec("parlay.min_books", "Minimum books per leg",
          "A consensus drawn from one book is that book's opinion plus its hold, not a "
          "market. Legs quoted by fewer books than this are excluded.",
          3.0, 1.0, 12.0, "parlay", step=1.0, kind="int", affects=_PARLAY),
    _spec("parlay.max_leg_price", "Maximum leg price (American)",
          "Longshot ceiling. Past roughly +800 the de-vig is dominated by how the book "
          "loads its hold onto the longshot, so the fair probability — and therefore the "
          "edge — is the least reliable number on the board.",
          800.0, 150.0, 5000.0, "parlay", step=50.0, kind="int", affects=_PARLAY),
    _spec("parlay.min_leg_price", "Minimum leg price (American)",
          "Chalk floor. Below about -2500 a leg contributes essentially no payout while "
          "remaining fully capable of losing the ticket, which is the most common way a "
          "retail parlay dies.",
          -2500.0, -20000.0, -150.0, "parlay", step=50.0, kind="int", affects=_PARLAY),
    _spec("parlay.kelly_cap", "Kelly stake cap",
          "Hard ceiling on a single ticket's suggested stake, as a fraction of bankroll. "
          "A parlay's probability is a product of N uncertain numbers, so even fractional "
          "Kelly on the point estimate over-bets; this cap is what stops one optimistic "
          "slate from mattering.",
          0.02, 0.001, 0.10, "parlay", step=0.001, affects=_PARLAY),
    _spec("parlay.kelly_fraction", "Kelly fraction",
          "Fraction of full Kelly to stake before the cap applies.",
          0.25, 0.05, 1.0, "parlay", step=0.05, affects=_PARLAY),
    _spec("parlay.max_leg_reuse", "Max tickets sharing a leg",
          "Caps how many recommended tickets may contain the same leg. Without it the "
          "board fills with near-identical tickets built on one anchor, which looks like "
          "diversification while being a single concentrated bet.",
          3.0, 1.0, 10.0, "parlay", step=1.0, kind="int", affects=_PARLAY),
    _spec("parlay.sgp_enabled", "Same-game legs",
          "Allow more than one leg from a single game in a hand-built ticket. Those legs "
          "are priced on the joint (margin, total) distribution — never by multiplying "
          "their prices, which misvalues a favourite-cover-plus-over pair by several "
          "points of probability. Turn off to restore one-leg-per-game everywhere.",
          1.0, 0.0, 1.0, "parlay", step=1.0, kind="int", affects=_PARLAY),
    _spec("parlay.sgp_max_legs", "Max legs from one game",
          "Ceiling on how many legs a single game may contribute. Each extra leg is "
          "another marginal the lattice fit has to satisfy, and past four the constraints "
          "start fighting each other while the payout is doing almost nothing.",
          4.0, 2.0, 5.0, "parlay", step=1.0, kind="int", affects=_PARLAY),
    _spec("parlay.alt_lines_enabled", "Alternate lines",
          "Build a leg at every number a book actually hangs, not only the one the most "
          "books agree on. Key-number value — a lone -2.5 against a -3.5 consensus, an "
          "over at 44.5 when the market is 45.5 — is the most repeatable edge in the NFL, "
          "and collapsing to the modal line throws it away for free. Costs no extra "
          "odds-feed credits: the quotes are already stored.",
          1.0, 0.0, 1.0, "parlay", step=1.0, kind="int", affects=_PARLAY),
    _spec("parlay.alt_line_max_offset", "Alternate line reach (points)",
          "How far from the consensus number an alternate line may sit and still be "
          "built. Far from the consensus the market-implied fair curve is being "
          "extrapolated on our sigma rather than read off a quote, and the further it "
          "goes the more the answer is the model's rather than the market's.",
          3.5, 0.5, 14.0, "parlay", step=0.5, affects=_PARLAY),
    _spec("parlay.alt_line_min_books_devig", "Books needed to de-vig an alt line",
          "An alternate line is de-vigged from its own two-way quote only when at least "
          "this many books hang both sides there; otherwise its fair price is read off "
          "the market-implied distribution. One book's two-way price at an off number is "
          "that book's hold, not a market, and de-vigging it manufactures edge.",
          2.0, 1.0, 8.0, "parlay", step=1.0, kind="int", affects=_PARLAY),
    _spec("parlay.alt_lines_per_market", "Alternate lines kept per market",
          "Cap on how many alternate numbers are built per market per game, nearest the "
          "consensus first. Keeps the leg pool — and the combinatorial search over it — "
          "from growing with the number of books rather than the number of bets.",
          4.0, 0.0, 12.0, "parlay", step=1.0, kind="int", affects=_PARLAY),
    # ---- Value board (single-bet edge finding) ---------------------------
    _spec("value.min_ev", "Minimum EV to recommend",
          "Expected value per unit staked a bet must clear to be called playable. Not "
          "arbitrary: the standard error on the fitted edge-trust lambda itself moves a "
          "leg's edge by roughly a point of probability, which at typical -110 pricing is "
          "about 2% of EV. Below that we cannot tell a real edge from the error bar on "
          "our estimate of how real our edges are.",
          0.02, 0.0, 0.20, "value", step=0.005, affects=_VALUE),
    _spec("value.min_p_edge_real", "Minimum confidence edge is real",
          "Required probability that the bet is genuinely +EV rather than estimation "
          "noise, computed from the residual uncertainty in a shrunk leg estimate. This "
          "is what separates a 4% edge on a number we know well from a 4% edge on a "
          "number we do not.",
          0.58, 0.50, 0.95, "value", step=0.01, affects=_VALUE),
    _spec("value.ml_price_floor", "Moneyline price floor",
          "Moneylines shorter than this are never recommended — they appear in the "
          "priced-out list with the reason instead. A product decision, not a model "
          "output: at -250 a bet must win 71% to break even, books cut limits hard on "
          "that side, and one bad estimate costs several times what a good one wins. "
          "This is the setting that stops heavy chalk being labelled an edge.",
          -250.0, -2000.0, -105.0, "value", step=5.0, kind="int", affects=_VALUE),
    _spec("value.max_price", "Longshot ceiling",
          "Prices longer than this are not recommended. Past here the de-vig is dominated "
          "by how the book distributes its hold onto the longshot rather than by what it "
          "thinks will happen, so the fair probability is the least reliable number on "
          "the board.",
          600.0, 150.0, 3000.0, "value", step=25.0, kind="int", affects=_VALUE),
    _spec("value.min_books", "Minimum books quoting",
          "A consensus drawn from fewer books than this is that book's opinion plus its "
          "hold. Legs below the threshold are priced and shown, but never recommended.",
          3.0, 1.0, 12.0, "value", step=1.0, kind="int", affects=_VALUE),
    _spec("value.min_stake_units", "Minimum stake to bother",
          "Picks that size below this many units (1 unit = 1% of bankroll) are demoted to "
          "'thin'. A bet that Kelly sizes at 0.05u is telling you not to place it.",
          0.15, 0.0, 2.0, "value", step=0.05, unit="u", affects=_VALUE),
    _spec("value.kelly_fraction", "Kelly fraction (single bets)",
          "Fraction of full Kelly staked on a single bet before the cap applies. Quarter "
          "Kelly is the standard concession to the fact that the probability being sized "
          "on is itself an estimate.",
          0.25, 0.05, 1.0, "value", step=0.05, affects=_VALUE),
    _spec("value.kelly_cap", "Kelly cap (single bets)",
          "Hard ceiling on a single bet's suggested stake as a fraction of bankroll. "
          "Higher than the parlay cap because a straight bet's probability is one "
          "estimate rather than a product of several.",
          0.03, 0.002, 0.10, "value", step=0.002, affects=_VALUE),
    _spec("value.tail_tau_inflation", "Tail uncertainty inflation",
          "How much to widen residual uncertainty toward the ends of the price curve. A "
          "single logit-scale tau fitted across the whole board asserts we know a 95% "
          "number as precisely as a 55% one; out there the deciding factors — starters "
          "pulled, garbage time, backdoor covers — are not in the model at all. Set to 0 "
          "to reproduce flat-tau behaviour exactly.",
          1.5, 0.0, 6.0, "value", step=0.1, affects=_VALUE),
    _spec("value.max_disagreement", "Model/market sanity limit",
          "Largest raw disagreement between the model and a multi-book consensus, on the "
          "log-odds scale, that is treated as an opinion rather than a broken input. A "
          "circuit breaker, not a tuning knob: 1.0 is roughly 52% to 74%, about seven "
          "points of spread, and nobody beats a consensus by seven points. A gap that big "
          "is a stale distribution, a mismatched line, or a team the model cannot rate — "
          "and without this gate it surfaces as a maximum-stake recommendation at 100% "
          "confidence. Raise it only if you have verified the inputs.",
          1.0, 0.2, 3.0, "value", step=0.05, affects=_VALUE),
    _spec("value.max_price_edge", "Bad-quote limit",
          "How much free money a single book quote may show against the market's own fair "
          "number before it is treated as a stale or mistyped row rather than a good "
          "price. EV and stake are computed from the best price across books — the input "
          "most sensitive to one bad row, while the consensus that prices it is a median "
          "and shrugs that row off. Shopping seven books buys a point or two; a quote "
          "showing double-digit free money at the consensus probability is wrong, and "
          "without this gate it becomes a maximum-stake recommendation.",
          0.12, 0.02, 1.0, "value", step=0.01, affects=_VALUE),
    _spec("value.band_prior_chalk", "Chalk edge-trust prior",
          "Multiplier on the global edge-trust lambda for legs the market prices at 80% "
          "or better, used until that band has enough settled picks to fit its own. Low "
          "on purpose: we have not demonstrated edge on heavy favourites, and this is "
          "labelled a prior rather than a finding. It rises on its own the moment the "
          "history shows the model beating closing lines out there.",
          0.35, 0.0, 1.0, "value", step=0.05, affects=_VALUE),
    _spec("value.band_prior_dog", "Longshot edge-trust prior",
          "Same, for legs the market prices below 35%. Discounted less than chalk but "
          "still below 1: the power de-vig corrects most of the book's hold-loading on "
          "the longshot, but the correction is itself an estimate.",
          0.65, 0.0, 1.0, "value", step=0.05, affects=_VALUE),
)

REGISTRY: dict[str, ParamSpec] = {s.key: s for s in _SPECS}

# Cross-param sanity rules: (lo_key, hi_key) pairs that must satisfy lo < hi.
_PAIR_RULES: tuple[tuple[str, str], ...] = (
    ("defense.clamp_lo", "defense.clamp_hi"),
    ("player.env_clamp_lo", "player.env_clamp_hi"),
    ("levers.team_ratio_clamp_lo", "levers.team_ratio_clamp_hi"),
    ("levers.tilt_clamp_lo", "levers.tilt_clamp_hi"),
    ("levers.share_ratio_clamp_lo", "levers.share_ratio_clamp_hi"),
    ("levers.eff_ratio_clamp_lo", "levers.eff_ratio_clamp_hi"),
    ("levers.snap_ratio_clamp_lo", "levers.snap_ratio_clamp_hi"),
    ("levers.availability_clamp_lo", "levers.availability_clamp_hi"),
    ("market.w_base", "market.w_cap"),
    ("weather.wind_mod_mph", "weather.wind_high_mph"),
    ("weather.precip_mod_in", "weather.precip_high_in"),
)


# ---- Value resolution -------------------------------------------------------


def _db_map() -> dict[str, float]:
    """Current DB overrides {key: value}; cached, fail-open to empty."""
    cached = cache.get(_MAP_KEY)
    if isinstance(cached, dict):
        return cached
    try:
        from ..db import SessionLocal
        from ..models.model_param import ModelParam
        with SessionLocal() as db:
            rows = db.query(ModelParam.key, ModelParam.value).all()
        m = {k: float(v) for k, v in rows if k in REGISTRY}
    except Exception:  # noqa: BLE001 — tuning layer must never break reads
        log.warning("param_registry: DB read failed; using code defaults", exc_info=True)
        return {}
    cache.set(_MAP_KEY, m, _MAP_TTL)
    return m


def invalidate() -> None:
    cache.delete(_MAP_KEY)
    cache.delete(_VERSION_KEY)


def value(key: str) -> float:
    """Effective value: preview overlay → DB override → code default."""
    spec = REGISTRY.get(key)
    if spec is None:
        raise KeyError(f"unknown model param: {key}")
    ov = _overlay.get()
    if ov is not None and key in ov:
        return spec.coerce(ov[key])
    v = _db_map().get(key)
    return spec.coerce(v) if v is not None else spec.default


def value_int(key: str) -> int:
    return int(round(value(key)))


def values(*keys: str) -> tuple[float, ...]:
    return tuple(value(k) for k in keys)


def effective_map() -> dict[str, float]:
    """{key: effective value} for every registered param."""
    return {k: value(k) for k in REGISTRY}


def overrides_map() -> dict[str, float]:
    """Only params whose effective value differs from the code default."""
    return {k: v for k, v in effective_map().items()
            if abs(v - REGISTRY[k].default) > 1e-12}


@contextlib.contextmanager
def overlay(params: dict[str, float]) -> Iterator[None]:
    """Context-local what-if values (impact preview). Never touches the DB."""
    clean = {k: float(v) for k, v in params.items() if k in REGISTRY}
    token = _overlay.set(clean)
    try:
        yield
    finally:
        _overlay.reset(token)


# ---- Version token (cache-buster for downstream caches) ---------------------


def _compute_version(db: Session) -> str:
    from ..models.model_param import ModelParam
    n, latest = db.query(func.count(ModelParam.id), func.max(ModelParam.updated_at)).one()
    if not n:
        return "mp0"
    stamp = latest.isoformat() if latest is not None else "0"
    return f"mp{n}-{stamp}"


def version(db: Session) -> str:
    """Token for downstream cache keys; changes on any param write. Fail-open.

    Overlay-aware: inside an impact-preview overlay the token becomes a stable
    hash of the what-if values, so every downstream cache keyed on it computes
    (and caches) preview results separately instead of serving stale boards.
    """
    ov = _overlay.get()
    if ov is not None:
        h = hashlib.md5(repr(sorted(ov.items())).encode()).hexdigest()[:10]
        return f"mp-preview-{h}"
    v = cache.get(_VERSION_KEY)
    if isinstance(v, str):
        return v
    try:
        v = _compute_version(db)
    except Exception:  # noqa: BLE001
        return "mp0"
    cache.set(_VERSION_KEY, v, _MAP_TTL)
    return v


# ---- Validation -------------------------------------------------------------


def validate(key: str, v: float, *, pending: dict[str, float] | None = None) -> ParamSpec:
    """Bounds + cross-param checks. Returns the spec; raises ValueError."""
    spec = REGISTRY.get(key)
    if spec is None:
        raise ValueError(f"unknown model param: {key}")
    if not math.isfinite(v):
        raise ValueError(f"{key}: value must be finite")
    if not spec.clamp_valid(v):
        raise ValueError(
            f"{key}: {v} outside allowed range [{spec.min}, {spec.max}]"
        )

    def _eff(k: str) -> float:
        if pending and k in pending:
            return pending[k]
        return v if k == key else value(k)

    for lo_k, hi_k in _PAIR_RULES:
        if key in (lo_k, hi_k):
            lo, hi = _eff(lo_k), _eff(hi_k)
            if lo >= hi:
                raise ValueError(f"{lo_k} ({lo}) must stay below {hi_k} ({hi})")
    return spec
