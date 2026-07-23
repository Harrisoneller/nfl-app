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
}


def _spec(key: str, label: str, desc: str, default: float, lo: float, hi: float,
          cat: str, *, step: float = 0.01, kind: str = "float", unit: str = "",
          affects: tuple[str, ...] = ()) -> ParamSpec:
    return ParamSpec(key=key, label=label, description=desc, default=default,
                     min=lo, max=hi, category=cat, step=step, kind=kind,
                     unit=unit, affects=affects)


_GAME = ("game predictions", "spreads", "totals", "win prob")
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
