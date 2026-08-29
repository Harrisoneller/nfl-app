"""Game predictions + season Monte Carlo simulator.

Both reads ratings out of `elo_service`. Per-game outputs include win prob,
predicted spread, and predicted total. Season simulation runs ~10k trials of
the remaining schedule and aggregates wins, division winner counts, and
playoff seed odds.
"""
from __future__ import annotations

import asyncio
import math
import random
from collections import Counter, defaultdict
from typing import Any

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..adapters.data.nfl_data_py_adapter import NflDataPyAdapter
from ..cache import cache
from ..logging_config import get_logger
from ..models.game import Game
from ..models.seed import NFL_TEAMS
from ..utils.seasons import current_or_upcoming_season, latest_completed_season
from ..utils.teams import canonical_team
from . import (
    analytics_service,
    artifact_cache,
    backtest_service,
    context_service,
    dist_model,
    elo_service,
    prediction_dist,
    uncertainty_service,
)

log = get_logger(__name__)
_nfl = NflDataPyAdapter()

CACHE_TTL = 60 * 30  # 30 minutes
PREDICTION_MODEL_VERSION = "epa-elo-ctx-v4"


# Total scoring: league avg points/game per team is ~22; vary with team scoring.
# Registry-backed ("game_model" / "distribution" categories): the module
# constants are import-safe fallbacks, the helpers below resolve live values.
LEAGUE_AVG_POINTS_PER_TEAM = 22.0

# Game-margin SD used to turn the point spread into an outcome distribution.
GAME_SIGMA = prediction_dist.NFL_MARGIN_SIGMA  # ~13.5 points


def _league_avg() -> float:
    from . import param_registry
    return param_registry.value("game.league_avg_points")


def _game_sigma() -> float:
    return prediction_dist.margin_sigma()


def _conditional_sigma_enabled() -> bool:
    """Whether to use the game-conditional sigma model.

    Kept as a switch because turning it on changes every probability the
    product quotes. If calibration regresses, this reverts the whole board from
    the admin panel in seconds rather than needing a deploy.
    """
    from . import param_registry
    try:
        return float(param_registry.value("dist.conditional_sigma_enabled")) >= 0.5
    except Exception:  # noqa: BLE001
        return True


def _clamp_sigma_mult(v: float) -> float:
    return max(0.90, min(1.35, float(v or 1.0)))


def _wind_inputs(weather: dict[str, Any] | None) -> tuple[float | None, bool]:
    """(wind_mph, indoor) from a weather_service forecast payload.

    Returns ``(None, False)`` for anything unusable. An unknown forecast must
    price identically to the pre-weather model rather than to a guessed
    average — a missing reading is not a calm day.
    """
    if not isinstance(weather, dict) or weather.get("available") is False:
        return None, False
    indoor = bool(
        weather.get("indoor")
        or weather.get("is_dome")
        or weather.get("roof") in ("dome", "closed")
    )
    if indoor:
        return None, True
    for key in ("wind_mph", "wind_speed", "windspeed", "wind"):
        v = weather.get(key)
        if v is not None:
            try:
                w = float(v)
            except (TypeError, ValueError):
                continue
            return (w if w > 0 else None), False
    return None, False

# Season-long latent-strength uncertainty per team (Elo points), drawn once per
# Monte Carlo trial and held across that team's whole slate. This is what makes
# the season win-total distribution correlated and realistically wide instead of
# an over-tight sum of independent coin flips. TUNABLE: validate against the
# backtest PIT histogram / observed win-total dispersion (see PREDICTION_MODEL_SPEC).
RATING_SIGMA_ELO = 55.0


def _rating_sigma() -> float:
    from . import param_registry
    return param_registry.value("game.rating_sigma_elo")


def _param(key: str) -> float:
    from . import param_registry
    return param_registry.value(key)


# Scale matching success-rate deviations to EPA/play deviations: league SR
# spread is ~±0.05, EPA/play spread ~±0.08, so 1.6·SR_dev sits on the EPA scale.
_SR_TO_EPA_SCALE = 1.6
# Pace can move a total by at most ±8% — pace is real but bounded.
_PACE_MULT_CLAMP = (0.92, 1.08)


def _lever_ratio(aggs: dict[str, Any] | None, *keys: str) -> float:
    """Admin input-lever passthrough for the EPA path.

    model_inputs_service records its PPG-space adjustments in
    ``_input_adjustment`` ({"points_per_game": {"from": x, "to": y}, ...});
    the fundamentals layer honors them as a multiplicative ratio so a
    coaching-change lever still moves EPA-based scores.
    """
    adj = (aggs or {}).get("_input_adjustment")
    if not isinstance(adj, dict):
        return 1.0
    ratio = 1.0
    for k in keys:
        entry = adj.get(k)
        if isinstance(entry, dict):
            frm, to = entry.get("from"), entry.get("to")
            if frm and to and float(frm) > 0:
                ratio *= float(to) / float(frm)
    return ratio


def _team_strengths(aggs: dict[str, Any] | None) -> tuple[float, float] | None:
    """(off_strength, def_strength) in EPA/play deviations, or None if the
    adjusted-EPA layer isn't available for this team.

    Offense: opponent-adjusted EPA/play blended with adjusted success rate
    (stability) plus a CPOE credit (QB accuracy sustains, box-score EPA doesn't).
    Defense: positive = allows more than league average = bad.
    """
    if not aggs:
        return None
    off_epa = aggs.get("adj_off_epa_per_play")
    def_epa = aggs.get("adj_def_epa_per_play")
    if off_epa is None or def_epa is None:
        return None
    w_sr = _param("game.success_rate_weight")
    off = (1 - w_sr) * float(off_epa) + w_sr * _SR_TO_EPA_SCALE * float(
        aggs.get("adj_off_success_rate") or 0.0
    )
    cpoe = aggs.get("off_cpoe")
    if cpoe is not None:
        off += _param("game.cpoe_epa_per_pct") * float(cpoe)
    deff = (1 - w_sr) * float(def_epa) + w_sr * _SR_TO_EPA_SCALE * float(
        aggs.get("adj_def_success_rate") or 0.0
    )
    return off, deff


def _fundamentals(
    home_aggs: dict[str, Any] | None,
    away_aggs: dict[str, Any] | None,
    neutral_site: bool,
) -> dict[str, Any] | None:
    """Adjusted-EPA fundamentals layer: expected points, margin, and total.

    Expected points = league avg + (own adjusted offense + opponent adjusted
    defense) · points-per-net-EPA — both effects come from one ridge fit, so
    this is the model's E[game EPA] mapped to points. Totals then get the two
    matchup-specific drivers PPG can't see: neutral-situation pace (seconds
    per snap → play volume) and combined PROE (pass-heavy → clock stops →
    more plays/points). Returns None when either team lacks adjusted metrics
    (cold start / missing PBP) — callers fall back to the PPG path.
    """
    hs = _team_strengths(home_aggs)
    as_ = _team_strengths(away_aggs)
    if hs is None or as_ is None:
        return None
    league_avg = _league_avg()
    ppe = _param("game.points_per_net_epa")

    exp_h = league_avg + (hs[0] + as_[1]) * ppe
    exp_a = league_avg + (as_[0] + hs[1]) * ppe
    # Admin lever passthrough (PPG-space levers scale the EPA path too).
    exp_h *= _lever_ratio(home_aggs, "points_per_game", "points_per_game_effect")
    exp_h *= _lever_ratio(away_aggs, "points_allowed_per_game", "points_allowed_per_game_effect")
    exp_a *= _lever_ratio(away_aggs, "points_per_game", "points_per_game_effect")
    exp_a *= _lever_ratio(home_aggs, "points_allowed_per_game", "points_allowed_per_game_effect")

    # Pace: harmonic-ish blend of both teams' neutral sec/snap vs league anchor.
    anchor = _param("game.league_neutral_sec_per_play")
    paces = [
        float(v) for v in (
            (home_aggs or {}).get("off_neutral_sec_per_play"),
            (away_aggs or {}).get("off_neutral_sec_per_play"),
        ) if v
    ]
    pace_mult = 1.0
    if paces:
        raw = (anchor / (sum(paces) / len(paces))) ** _param("game.pace_elasticity")
        pace_mult = min(max(raw, _PACE_MULT_CLAMP[0]), _PACE_MULT_CLAMP[1])

    proe_h = float((home_aggs or {}).get("off_proe") or 0.0)
    proe_a = float((away_aggs or {}).get("off_proe") or 0.0)
    proe_pts = (proe_h + proe_a) * _param("game.proe_total_pts")

    exp_h = exp_h * pace_mult + proe_pts / 2
    exp_a = exp_a * pace_mult + proe_pts / 2

    hfa_pts = 0.0 if neutral_site else (
        elo_service.HOME_FIELD_ADVANTAGE / _param("elo.elo_per_point")
    )
    return {
        "expected_home_pts": exp_h,
        "expected_away_pts": exp_a,
        "margin": (exp_h - exp_a) + hfa_pts,
        "total": exp_h + exp_a,
        "home_strength": {"off": round(hs[0], 4), "def": round(hs[1], 4)},
        "away_strength": {"off": round(as_[0], 4), "def": round(as_[1], 4)},
        "pace_multiplier": round(pace_mult, 4),
        "proe_total_pts": round(proe_pts, 2),
        "hfa_pts": round(hfa_pts, 2),
    }


def _build_explainability(
    *,
    home_rating: float,
    away_rating: float,
    neutral_site: bool,
    home_off_ppg: float,
    away_off_ppg: float,
    home_def_ppg_allowed: float,
    away_def_ppg_allowed: float,
    fundamentals: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    wind_total_pts: float = 0.0,
) -> dict[str, Any]:
    """Feature-contribution heuristic for the prediction UI.

    v2: when the adjusted-EPA layer is live, contributors are stated in its
    terms (opponent-adjusted efficiency, pace/PROE environment); otherwise the
    legacy PPG heuristic applies.
    """
    hfa = 0.0 if neutral_site else elo_service.HOME_FIELD_ADVANTAGE
    rating_edge = home_rating + hfa - away_rating

    contributors = [
        {
            "feature": "elo_rating_gap",
            "label": "Elo + home-field gap",
            "impact": round(rating_edge / 28.0, 2),
            "direction": "home" if rating_edge >= 0 else "away",
        },
    ]
    if fundamentals:
        h, a = fundamentals["home_strength"], fundamentals["away_strength"]
        # Net matchup edge in EPA/play: my offense vs their defense, both ways.
        epa_edge = (h["off"] + a["def"]) - (a["off"] + h["def"])
        env = (fundamentals["pace_multiplier"] - 1.0) * 44.0 + fundamentals["proe_total_pts"]
        contributors += [
            {
                "feature": "adjusted_epa_edge",
                "label": "Opponent-adjusted efficiency edge (EPA/play)",
                "impact": round(epa_edge / 0.08, 2),
                "direction": "home" if epa_edge >= 0 else "away",
            },
            {
                "feature": "defensive_resistance_gap",
                "label": "Adjusted defensive edge",
                "impact": round((a["def"] - h["def"]) / 0.06, 2),
                "direction": "home" if (a["def"] - h["def"]) >= 0 else "away",
            },
            {
                "feature": "game_pace_environment",
                "label": "Pace + pass-rate environment (total)",
                "impact": round(env / 3.0, 2),
                "direction": "over" if env >= 0 else "under",
            },
        ]
        method = "adjusted_epa_v2"
        summary = ("Impacts from opponent-adjusted EPA/success rate (ridge), CPOE, "
                   "and neutral-situation pace/PROE, with Elo as the prior.")
    else:
        home_matchup_edge = home_off_ppg - away_def_ppg_allowed
        away_matchup_edge = away_off_ppg - home_def_ppg_allowed
        scoring_edge = home_matchup_edge - away_matchup_edge
        pace_bias = (home_off_ppg + away_off_ppg) - (2 * _league_avg())
        defense_resistance_edge = away_def_ppg_allowed - home_def_ppg_allowed
        contributors += [
            {
                "feature": "offense_vs_defense_gap",
                "label": "Offense vs opposing defense",
                "impact": round(scoring_edge / 2.8, 2),
                "direction": "home" if scoring_edge >= 0 else "away",
            },
            {
                "feature": "defensive_resistance_gap",
                "label": "Defensive resistance edge",
                "impact": round(defense_resistance_edge / 2.2, 2),
                "direction": "home" if defense_resistance_edge >= 0 else "away",
            },
            {
                "feature": "game_pace_environment",
                "label": "Expected scoring environment",
                "impact": round(pace_bias / 6.0, 2),
                "direction": "home" if pace_bias >= 0 else "away",
            },
        ]
        method = "heuristic_inputs_v1"
        summary = "Directional feature impacts estimated from Elo and scoring tendency inputs."
    # Context enters as its own contributor, scaled so that ~2.5 points of
    # context reads as comparably important to a 28-point Elo gap. It competes
    # for a top-three slot on merit: when a starting QB is out, that IS the
    # story of the game and it should outrank the efficiency edge.
    ctx_pts = float((context or {}).get("points") or 0.0)
    if context and abs(ctx_pts) >= 0.05:
        contributors.append({
            "feature": "context_layer",
            "label": _context_label(context),
            "impact": round(ctx_pts / 2.5, 2),
            "direction": "home" if ctx_pts >= 0 else "away",
        })
    if wind_total_pts and abs(wind_total_pts) >= 0.25:
        contributors.append({
            "feature": "wind",
            "label": "Wind (total)",
            "impact": round(wind_total_pts / 3.0, 2),
            "direction": "under",
        })

    contributors.sort(key=lambda x: abs(float(x["impact"])), reverse=True)
    out = {
        "method": method,
        "summary": summary,
        "top_contributors": contributors[:3],
    }
    # Per-component context breakdown, kept out of the top-three competition so
    # a reader can always see every component that fired, not just the loudest.
    # A number nobody can decompose is a number nobody can audit.
    if context:
        out["context"] = {
            "net_points": round(ctx_pts, 2),
            "sigma_mult": context.get("sigma_mult"),
            "confidence": context.get("confidence"),
            "capped": context.get("capped"),
            "components": _context_components(context),
        }
    return out


def _context_components(context: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten both sides' context components into one home-signed list."""
    rows: list[dict[str, Any]] = []
    for side, sign in (("home", 1.0), ("away", -1.0)):
        entry = context.get(side) or {}
        for c in entry.get("components") or []:
            rows.append({
                "side": side,
                "team_id": entry.get("team_id"),
                "component": c.get("component"),
                "points_home_signed": round(float(c.get("points") or 0.0) * sign, 2),
                "sigma_mult": c.get("sigma_mult"),
                "confidence": c.get("confidence"),
                "source": c.get("source"),
                "detail": c.get("detail"),
                "captured_at": c.get("captured_at"),
            })
    for c in context.get("matchup") or []:
        rows.append({
            "side": "matchup",
            "component": c.get("component"),
            "points_home_signed": round(float(c.get("points") or 0.0), 2),
            "sigma_mult": c.get("sigma_mult"),
            "confidence": c.get("confidence"),
            "source": c.get("source"),
            "detail": c.get("detail"),
            "captured_at": c.get("captured_at"),
        })
    rows.sort(key=lambda r: abs(float(r["points_home_signed"])), reverse=True)
    return rows


def _context_label(context: dict[str, Any]) -> str:
    """Name the dominant context component so the label says something real."""
    comps = _context_components(context)
    if not comps:
        return "Game context"
    top = comps[0].get("component") or "context"
    return {
        "availability": "Injuries / availability",
        "qb": "Quarterback change",
        "staff_change": "Coaching change",
        "scheme_matchup": "Scheme matchup",
        "situational": "Rest / travel / situation",
        "weather": "Weather",
        "manual": "Analyst adjustment",
    }.get(top, "Game context")


def predict_game(
    home_rating: float, away_rating: float,
    home_off_ppg: float | None = None, away_off_ppg: float | None = None,
    home_def_ppg_allowed: float | None = None, away_def_ppg_allowed: float | None = None,
    neutral_site: bool = False,
    home_aggs: dict[str, Any] | None = None,
    away_aggs: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    weather: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Single-game predictor: Elo prior × adjusted-EPA fundamentals × context.

    Margin: blend of the Elo-implied margin (market-anchored prior) and the
    fundamentals margin from opponent-adjusted EPA/success rate/CPOE, weighted
    by ``game.w_fundamentals``, then shifted by the context layer. Total:
    fundamentals expected points shaped by neutral-situation pace and PROE,
    then by wind. When either team lacks adjusted metrics (cold start),
    everything degrades to the legacy Elo + PPG path.

    ``context`` is the home-perspective payload from
    ``context_service.game_context`` — what the historical inputs cannot see
    (who is actually playing, who is coaching, the situation). It contributes a
    signed point shift AND a sigma multiplier: missing information should widen
    the distribution, not only move its center.

    ``weather`` is the forecast dict from ``weather_service``. Only wind is
    priced, and only into the total and the two sigmas — see ``dist_model``.
    """
    elo_margin = -elo_service.predicted_spread(home_rating, away_rating, neutral_site)
    fund = _fundamentals(home_aggs, away_aggs, neutral_site)
    w_fund = _param("game.w_fundamentals") if fund else 0.0
    expected_margin = (1 - w_fund) * elo_margin + w_fund * (fund["margin"] if fund else 0.0)

    ctx = context if isinstance(context, dict) and context.get("applicable") else None
    margin_before_context = expected_margin
    if ctx:
        expected_margin += float(ctx.get("points") or 0.0)

    # Spread is quoted AFTER context: the number we publish has to be the
    # number we actually believe, not the historical one with a footnote.
    spread = -expected_margin  # negative = home favored (sportsbook convention)

    wind_mph, indoor = _wind_inputs(weather)

    league_avg = _league_avg()
    h_off = home_off_ppg if home_off_ppg is not None else league_avg
    a_off = away_off_ppg if away_off_ppg is not None else league_avg
    h_def = home_def_ppg_allowed if home_def_ppg_allowed is not None else league_avg
    a_def = away_def_ppg_allowed if away_def_ppg_allowed is not None else league_avg
    if fund:
        expected_home_pts = fund["expected_home_pts"]
        expected_away_pts = fund["expected_away_pts"]
        total = fund["total"]
    else:
        # Legacy PPG path: a team's own scoring, nudged by how much more/less
        # than a league-average defense the opponent allows.
        expected_home_pts = h_off + (a_def - league_avg)
        expected_away_pts = a_off + (h_def - league_avg)
        total = expected_home_pts + expected_away_pts

    # Wind is the one weather variable with a large, reliable effect. It comes
    # off the total and out of both sigmas; temperature and precipitation are
    # deliberately not priced (see dist_model).
    wind_total_pts = dist_model.wind_total_points(wind_mph, indoor)
    if wind_total_pts:
        total += wind_total_pts
        share = expected_home_pts / (expected_home_pts + expected_away_pts) \
            if (expected_home_pts + expected_away_pts) > 0 else 0.5
        expected_home_pts += wind_total_pts * share
        expected_away_pts += wind_total_pts * (1.0 - share)

    # The game-conditional joint distribution. Every probability below is
    # derived from this one object, so spread, win prob, total and score ranges
    # cannot disagree with each other. The flat-sigma path stays available as
    # an instant admin revert because this changes every number on the board.
    ctx_sigma = float((ctx or {}).get("sigma_mult") or 1.0)
    if _conditional_sigma_enabled():
        gd = dist_model.build_game_distribution(
            expected_margin, total,
            pace_multiplier=(fund or {}).get("pace_multiplier") or 1.0,
            context_sigma_mult=ctx_sigma,
            wind_mph=wind_mph,
            indoor=indoor,
        )
        game_sigma = gd.sigma_m
        total_sd = gd.sigma_t
        rho = gd.rho
        win_p = gd.win_prob()
        sigma_model = gd.meta
    else:
        game_sigma = _game_sigma() * _clamp_sigma_mult(ctx_sigma)
        total_sd = prediction_dist.total_sigma()
        rho = math.tanh(expected_margin / 17.0) * 0.34
        win_p = prediction_dist.win_prob(expected_margin, game_sigma)
        sigma_model = {"mode": "flat", "margin_sigma": round(game_sigma, 2)}

    # Reconcile the margin with the total for displayed scores.
    predicted_home_score = (total + expected_margin) / 2
    predicted_away_score = (total - expected_margin) / 2

    # Outcome distribution — the "likely outcomes" view. Margin credible
    # intervals translate to score ranges (total held at its expectation).
    m_lo80, m_hi80 = prediction_dist.margin_interval(expected_margin, game_sigma, 0.80)
    m_lo50, m_hi50 = prediction_dist.margin_interval(expected_margin, game_sigma, 0.50)

    # Game-script label: shootout / methodical / defensive based on total + spread.
    abs_spread = abs(spread)
    if total >= 48:
        script = "Shootout"
    elif total <= 40:
        script = "Defensive grind"
    elif abs_spread >= 7:
        script = "Blowout potential"
    elif abs_spread <= 2.5:
        script = "Toss-up"
    else:
        script = "Methodical"
    return {
        "home_win_prob": round(win_p, 3),
        "away_win_prob": round(1 - win_p, 3),
        "predicted_spread": round(spread, 1),       # negative = home favored
        "predicted_total": round(total, 1),
        "predicted_home_score": round(predicted_home_score, 1),
        "predicted_away_score": round(predicted_away_score, 1),
        "game_script": script,
        "margin_sd": round(game_sigma, 2),
        # Full outcome distribution so the UI can show honest ranges, not just a point.
        "distribution": {
            "expected_margin": round(expected_margin, 1),
            "expected_total": round(total, 1),
            "margin_sd": round(game_sigma, 2),
            "total_sd": round(total_sd, 2),
            "margin_total_rho": round(rho, 3),
            "sigma_model": sigma_model,
            "home_win_prob": round(win_p, 3),
            "margin_interval_50": [round(m_lo50, 1), round(m_hi50, 1)],
            "margin_interval_80": [round(m_lo80, 1), round(m_hi80, 1)],
            "home_score_range_80": [round((total + m_lo80) / 2, 1), round((total + m_hi80) / 2, 1)],
            "away_score_range_80": [round((total - m_hi80) / 2, 1), round((total - m_lo80) / 2, 1)],
        },
        # Surface every input so the UI can render an "explain this" popover
        "inputs": {
            "home_elo": round(home_rating, 1),
            "away_elo": round(away_rating, 1),
            "home_field_advantage_elo": 0 if neutral_site else elo_service.HOME_FIELD_ADVANTAGE,
            "neutral_site": neutral_site,
            "home_off_ppg": round(h_off, 1),
            "away_off_ppg": round(a_off, 1),
            "home_def_ppg_allowed": round(h_def, 1),
            "away_def_ppg_allowed": round(a_def, 1),
            "league_avg_points": league_avg,
            "expected_home_pts": round(expected_home_pts, 1),
            "expected_away_pts": round(expected_away_pts, 1),
            # Adjusted-EPA fundamentals layer (None = PPG fallback in effect)
            "fundamentals": (
                {
                    "w_fundamentals": round(w_fund, 2),
                    "elo_margin": round(elo_margin, 1),
                    "fundamentals_margin": round(fund["margin"], 1),
                    "home_adj_off_epa": fund["home_strength"]["off"],
                    "home_adj_def_epa": fund["home_strength"]["def"],
                    "away_adj_off_epa": fund["away_strength"]["off"],
                    "away_adj_def_epa": fund["away_strength"]["def"],
                    "home_cpoe": (home_aggs or {}).get("off_cpoe"),
                    "away_cpoe": (away_aggs or {}).get("off_cpoe"),
                    "home_proe": (home_aggs or {}).get("off_proe"),
                    "away_proe": (away_aggs or {}).get("off_proe"),
                    "pace_multiplier": fund["pace_multiplier"],
                    "proe_total_pts": fund["proe_total_pts"],
                }
                if fund else None
            ),
            # Context layer (None = no context available for this game)
            "context": (
                {
                    "points": ctx.get("points"),
                    "points_uncapped": ctx.get("points_uncapped"),
                    "capped": ctx.get("capped"),
                    "sigma_mult": ctx.get("sigma_mult"),
                    "confidence": ctx.get("confidence"),
                    "margin_before_context": round(margin_before_context, 1),
                    "home": ctx.get("home"),
                    "away": ctx.get("away"),
                    "matchup": ctx.get("matchup"),
                }
                if ctx else None
            ),
            "weather": (
                {
                    "wind_mph": wind_mph,
                    "indoor": indoor,
                    "total_points_effect": round(wind_total_pts, 2),
                }
                if (wind_mph or indoor) else None
            ),
        },
        "explainability": _build_explainability(
            home_rating=home_rating,
            away_rating=away_rating,
            neutral_site=neutral_site,
            home_off_ppg=h_off,
            away_off_ppg=a_off,
            home_def_ppg_allowed=h_def,
            away_def_ppg_allowed=a_def,
            fundamentals=fund,
            context=ctx,
            wind_total_pts=wind_total_pts,
        ),
    }


# ---- Season-level: read schedule once, simulate ----------------------------


def _schedule_from_db(db: Session, season: int) -> pd.DataFrame | None:
    """Read the schedule from the games table (fast, no network).

    Deduplicates by (week, home_team, away_team) — ESPN scoreboard and nflverse
    can both create records for the same real-world game with different IDs.
    Prefers the nflverse record (game_id like "2026_01_AWAY_HOME") because it
    has accurate NULL scores for unplayed games (ESPN reports 0).
    """
    stmt = select(Game).where(Game.season == season, Game.season_type == 2)
    games = db.execute(stmt).scalars().all()
    if not games:
        return None

    seen: dict[tuple, dict] = {}
    for g in games:
        key = (g.week, g.home_team_id, g.away_team_id)
        is_nflverse_id = g.id and "_" in g.id and g.id[:4].isdigit()
        row = {
            "game_id": g.id,
            "season": g.season,
            "week": g.week,
            "home_team": g.home_team_id,
            "away_team": g.away_team_id,
            "home_score": g.home_score,
            "away_score": g.away_score,
            "gameday": g.start_time.strftime("%Y-%m-%d") if g.start_time else "",
            "gametime": g.start_time.strftime("%H:%M") if g.start_time else "",
            "game_type": "REG",
            "_is_nflverse": is_nflverse_id,
        }
        existing = seen.get(key)
        if existing is None:
            seen[key] = row
        elif is_nflverse_id and not existing.get("_is_nflverse"):
            seen[key] = row
        elif not is_nflverse_id and existing.get("_is_nflverse"):
            pass  # keep existing nflverse record
        elif g.home_score is None and existing.get("home_score") is not None:
            seen[key] = row

    rows = [{k: v for k, v in r.items() if k != "_is_nflverse"} for r in seen.values()]
    return pd.DataFrame(rows) if rows else None


async def _season_schedule(season: int, db: Session | None = None) -> pd.DataFrame | None:
    """Get schedule from DB first (synced by worker), fall back to nflverse."""
    if db is not None:
        df = _schedule_from_db(db, season)
        if df is not None and len(df) > 0:
            df["home_team"] = df["home_team"].map(lambda x: canonical_team(x) if isinstance(x, str) else x)
            df["away_team"] = df["away_team"].map(lambda x: canonical_team(x) if isinstance(x, str) else x)
            return df

    df = await _nfl.schedules_df(season)
    if df is None or len(df) == 0:
        return None
    df = df.copy()
    df["home_team"] = df["home_team"].map(lambda x: canonical_team(x) if isinstance(x, str) else x)
    df["away_team"] = df["away_team"].map(lambda x: canonical_team(x) if isinstance(x, str) else x)
    return df


async def predict_week(db: Session, season: int, week: int | None = None) -> dict[str, Any]:
    """Predictions for every game in the given week.

    If `week` is None, picks the next upcoming week (lowest week with unplayed games).
    Cached so the home-page hero isn't racing an 8–20s cold compute.
    """
    cache_key = f"{season}:{week if week is not None else 'next'}:{PREDICTION_MODEL_VERSION}"

    async def _compute() -> dict[str, Any]:
        return await _predict_week_uncached(db, season, week)

    payload = await artifact_cache.get_or_compute(
        kind="week_predictions",
        key=cache_key,
        compute=_compute,
        ttl_seconds=CACHE_TTL,
        l1_ttl_seconds=60,
    )
    if isinstance(payload, dict):
        return payload
    return await _predict_week_uncached(db, season, week)


async def _predict_week_uncached(
    db: Session, season: int, week: int | None = None,
) -> dict[str, Any]:
    """Predictions for every game in the given week (uncached)."""
    sched = await _season_schedule(season, db=db)
    if sched is None:
        return {"season": season, "week": None, "games": []}

    if week is None:
        # First week with at least one unplayed game
        unplayed = sched[sched["home_score"].isna() | sched["away_score"].isna()]
        if len(unplayed) == 0:
            return {"season": season, "week": None, "games": []}
        week = int(unplayed["week"].min())

    ratings = elo_service.current_ratings(db, season=season) or elo_service.current_ratings(db)
    # Pull team scoring tendencies once for the season; fall back to previous if empty.
    aggs = await analytics_service._team_pbp_aggregates(season, allow_live_fallback=False)
    if not aggs:
        aggs = await analytics_service._team_pbp_aggregates(season - 1, allow_live_fallback=False)
    # Admin model-input levers (pace / yards-per-play / pass-rate / PPG) adjust
    # the scoring inputs BEFORE prediction — a coaching-change lever moves
    # totals, scores, and game scripts through the normal pipeline.
    from . import model_inputs_service

    aggs = model_inputs_service.adjusted_team_aggregates(db, season, aggs or {})
    games = sched[sched["week"] == week]
    if "game_type" in games.columns:
        games = games[games["game_type"].astype(str).str.upper() == "REG"]
    out = []
    calibration_score, expected_calibration_error = await _calibration_context(db)
    # Market-aware layer: de-vigged multi-book consensus (+ Kalshi when
    # reachable) fetched once for the whole slate. Best-effort — an empty
    # context leaves every game on model-only numbers.
    from . import market_service  # late import: market_service ← prediction_dist only

    try:
        market_ctx = await market_service.week_market_context(db)
    except Exception as e:  # noqa: BLE001 — market context must never take down predictions
        log.warning("market_context_failed", error=str(e)[:200])
        market_ctx = {}

    # Context layer: one bundle for the whole slate (what the historical inputs
    # cannot see). Best-effort — an empty bundle leaves every game on the pure
    # historical numbers, which is exactly the pre-context behavior.
    try:
        ctx_bundle = context_service.week_context(db, season, week)
    except Exception as e:  # noqa: BLE001
        log.warning("context_bundle_failed", season=season, week=week,
                    error=str(e)[:200])
        ctx_bundle = None

    # Forecasts for the slate. Wind is the only variable the game model prices.
    weather_by_game: dict[str, dict[str, Any]] = {}
    try:
        from . import weather_service

        weather_by_game = await weather_service.forecasts_for_games([
            {
                "id": str(gg.get("game_id") or ""),
                "home_team_id": gg.get("home_team"),
                "gameday": gg.get("gameday"),
            }
            for _, gg in games.iterrows()
        ])
    except Exception as e:  # noqa: BLE001 — a forecast outage must not stop predictions
        log.warning("weather_context_failed", error=str(e)[:200])
        weather_by_game = {}

    for _, g in games.iterrows():
        h, a = g["home_team"], g["away_team"]
        if not h or not a:
            continue
        hr = ratings.get(h, elo_service.INITIAL_RATING)
        ar = ratings.get(a, elo_service.INITIAL_RATING)
        h_off = (aggs.get(h) or {}).get("points_per_game")
        a_off = (aggs.get(a) or {}).get("points_per_game")
        h_def = (aggs.get(h) or {}).get("points_allowed_per_game")
        a_def = (aggs.get(a) or {}).get("points_allowed_per_game")
        pred = predict_game(hr, ar, home_off_ppg=h_off, away_off_ppg=a_off,
                            home_def_ppg_allowed=h_def, away_def_ppg_allowed=a_def,
                            home_aggs=aggs.get(h), away_aggs=aggs.get(a),
                            context=context_service.game_context(ctx_bundle, h, a),
                            weather=weather_by_game.get(str(g.get("game_id") or "")))
        pred = uncertainty_service.attach_uncertainty(
            pred,
            model_version=PREDICTION_MODEL_VERSION,
            expected_calibration_error=expected_calibration_error,
        )
        # Headline numbers become the market blend when consensus exists;
        # pure-model values move to pred["model_only"] with pred["edge"]
        # exposing the disagreement. Admin overrides (below) still win.
        market_service.apply_market_blend(
            pred, market_service.context_for_game(market_ctx, h, a),
        )
        explainability = pred.get("explainability")
        if isinstance(explainability, dict):
            explainability["confidence_context"] = {
                "tier": pred.get("confidence_tier"),
                "calibration_score": pred.get("calibration_score"),
                "expected_calibration_error": pred.get("expected_calibration_error"),
                "interval_80_home_win_prob": pred.get("home_win_prob_interval_80"),
            }
        pred["global_calibration_score"] = calibration_score
        out.append({
            "id": str(g.get("game_id") or ""),
            "season": season,
            "week": week,
            "gameday": str(g.get("gameday") or ""),
            "gametime": str(g.get("gametime") or ""),
            "home_team_id": h,
            "away_team_id": a,
            "home_score": _safe_int(g.get("home_score")),
            "away_score": _safe_int(g.get("away_score")),
            "home_elo": round(hr, 1),
            "away_elo": round(ar, 1),
            "prediction": pred,
        })
    # Admin override layer — hand-set spread/total/win-prob supersede the
    # model at read time (see services/overrides_service.py). Late import:
    # overrides_service ← player_projection_engine only, no cycle, but keep
    # this module importable standalone in scripts.
    from . import overrides_service

    overrides_service.apply_week_game_overrides(db, season, week, out)
    return {"season": season, "week": week, "games": out}


def _reg_weeks_from_db(db: Session, season: int) -> list[dict[str, int]]:
    """Regular-season weeks present on the games table, with game counts."""
    rows = db.execute(
        select(Game.week, func.count())
        .where(Game.season == season, Game.season_type == 2, Game.week.is_not(None))
        .group_by(Game.week)
        .order_by(Game.week)
    ).all()
    return [{"week": int(w), "games": int(n)} for w, n in rows if w is not None]


def _reg_weeks_from_sched(sched: pd.DataFrame | None) -> list[dict[str, int]]:
    if sched is None or len(sched) == 0 or "week" not in sched.columns:
        return []
    games = sched
    if "game_type" in games.columns:
        games = games[games["game_type"].astype(str).str.upper() == "REG"]
    if len(games) == 0:
        return []
    counts = games.groupby("week").size().sort_index()
    return [{"week": int(w), "games": int(n)} for w, n in counts.items() if pd.notna(w)]


def _next_unplayed_reg_week(sched: pd.DataFrame | None) -> int | None:
    """Lowest REG week with an unplayed game; last REG week if the slate is final."""
    if sched is None or len(sched) == 0 or "week" not in sched.columns:
        return None
    games = sched
    if "game_type" in games.columns:
        games = games[games["game_type"].astype(str).str.upper() == "REG"]
    if len(games) == 0:
        return None
    unplayed = games[games["home_score"].isna() | games["away_score"].isna()]
    if len(unplayed) == 0:
        return int(games["week"].max())
    return int(unplayed["week"].min())


def _flatten_slate_game(g: dict[str, Any]) -> dict[str, Any]:
    """Lift nested prediction/market/edge fields for the week-page UI."""
    p = g.get("prediction") or {}
    market = p.get("market") if isinstance(p.get("market"), dict) else {}
    edge = p.get("edge") if isinstance(p.get("edge"), dict) else {}
    model_only = p.get("model_only") if isinstance(p.get("model_only"), dict) else {}
    dist = p.get("distribution") if isinstance(p.get("distribution"), dict) else {}
    home_elo = g.get("home_elo")
    away_elo = g.get("away_elo")
    elo_gap = None
    if isinstance(home_elo, (int, float)) and isinstance(away_elo, (int, float)):
        elo_gap = round(float(home_elo) - float(away_elo), 1)
    return {
        "id": g.get("id"),
        "season": g.get("season"),
        "week": g.get("week"),
        "gameday": g.get("gameday"),
        "gametime": g.get("gametime"),
        "home_team_id": g.get("home_team_id"),
        "away_team_id": g.get("away_team_id"),
        "home_score": g.get("home_score"),
        "away_score": g.get("away_score"),
        "home_elo": home_elo,
        "away_elo": away_elo,
        "elo_gap": elo_gap,
        "model_spread": p.get("predicted_spread"),
        "model_total": p.get("predicted_total"),
        "model_home_score": p.get("predicted_home_score"),
        "model_away_score": p.get("predicted_away_score"),
        "model_home_win_prob": p.get("home_win_prob"),
        "model_raw_spread": model_only.get("predicted_spread"),
        "predicted_spread": p.get("predicted_spread"),
        "predicted_total": p.get("predicted_total"),
        "predicted_home_score": p.get("predicted_home_score"),
        "predicted_away_score": p.get("predicted_away_score"),
        "home_win_prob": p.get("home_win_prob"),
        "market_spread": market.get("spread_home"),
        "market_total": market.get("total"),
        "spread_edge": edge.get("spread"),
        "total_edge": edge.get("total"),
        "confidence_tier": p.get("confidence_tier"),
        "game_script": p.get("game_script"),
        "distribution": dist,
        "market": market,
        "model_only": model_only,
        "edge": edge,
    }


async def week_slate(
    db: Session, season: int | None = None, week: int | None = None,
) -> dict[str, Any]:
    """Weekly rundown: model vs market for every REG game on the slate.

    `week=None` resolves to the next regular-season week with unplayed games
    (skips preseason / playoff weeks so the board is never an empty REG filter
    of a POST week number).
    """
    season = season or current_or_upcoming_season()
    sched = await _season_schedule(season, db=db)
    weeks = _reg_weeks_from_db(db, season) or _reg_weeks_from_sched(sched)
    resolved = week if week is not None else _next_unplayed_reg_week(sched)
    payload = await predict_week(db, season, resolved)
    games = [_flatten_slate_game(g) for g in (payload.get("games") or [])]
    games.sort(key=lambda g: (str(g.get("gameday") or ""), str(g.get("away_team_id") or "")))
    return {
        "season": payload.get("season") or season,
        "week": payload.get("week") if payload.get("week") is not None else resolved,
        "n_games": len(games),
        "model_version": PREDICTION_MODEL_VERSION,
        "weeks": weeks,
        "partial": bool(payload.get("partial")),
        "games": games,
    }


async def _calibration_context(db: Session) -> tuple[float, float | None]:
    """Load calibration metadata from backtest artifacts.

    Calibration is decoration on top of a prediction, never a precondition for
    one — this must degrade to defaults rather than fail the whole slate.
    """
    try:
        backtest = await backtest_service.backtest_elo(db)
        overall = backtest.get("overall", {})
        ece = overall.get("expected_calibration_error")
        score = uncertainty_service.calibration_score_from_ece(ece)
        return score, ece
    except asyncio.CancelledError:
        # BaseException, so `except Exception` misses it. Honor a real
        # cancellation of our own task; otherwise degrade (see artifact_cache
        # single-flight notes) instead of taking down /predictions/games.
        task = asyncio.current_task()
        if task is not None and task.cancelling() > 0:
            raise
        log.warning("prediction_calibration_lookup_cancelled")
        return 0.5, None
    except Exception as e:  # noqa: BLE001
        log.warning("prediction_calibration_lookup_failed", error=str(e)[:200])
        return 0.5, None


# ---- Monte Carlo ----------------------------------------------------------

DIVISIONS = {}
for t in NFL_TEAMS:
    DIVISIONS[t["id"]] = (t["conference"], t["division"])


async def simulate_season(
    db: Session, season: int, n_sims: int = 10_000,
) -> dict[str, Any]:
    """Run N simulations of the remaining schedule.

    Persisted to model_artifacts so the same simulation result is shared
    across all workers/processes and survives backend restarts. Refreshed
    daily by the scheduler.
    """
    # Two-layer cached fetch — L1 in-process, L2 Postgres
    artifact_key = f"season:{season}:n:{n_sims}"

    async def _compute() -> dict[str, Any]:
        return await _simulate_season_compute(db, season, n_sims)

    return await artifact_cache.get_or_compute(
        kind="monte_carlo_sim",
        key=artifact_key,
        compute=_compute,
        ttl_seconds=60 * 60 * 24,  # 24h
        l1_ttl_seconds=60 * 30,
    )


async def _simulate_season_compute(
    db: Session, season: int, n_sims: int,
) -> dict[str, Any]:
    """The real Monte Carlo. Separated so artifact_cache can wrap it."""
    sched = await _season_schedule(season, db=db)
    if sched is None:
        return {"season": season, "n_sims": 0, "teams": {}}

    ratings = elo_service.current_ratings(db, season=season) or elo_service.current_ratings(db)
    if not ratings:
        # Cold start — use defaults
        ratings = {t["id"]: elo_service.INITIAL_RATING for t in NFL_TEAMS}

    # Banked results from completed games (wins + point differential).
    banked_wins: dict[str, int] = defaultdict(int)
    banked_pd: dict[str, float] = defaultdict(float)
    pending: list[dict[str, Any]] = []
    for _, g in sched.iterrows():
        h, a = g["home_team"], g["away_team"]
        if not h or not a:
            continue
        hs, as_ = g.get("home_score"), g.get("away_score")
        if pd.notna(hs) and pd.notna(as_):
            margin = float(hs) - float(as_)
            banked_pd[h] += margin; banked_pd[a] -= margin
            if margin > 0:
                banked_wins[h] += 1
            elif margin < 0:
                banked_wins[a] += 1
        else:
            pending.append({
                "home": h, "away": a,
                "neutral": bool(g.get("location") == "Neutral") if "location" in sched.columns else False,
            })

    # Counters
    win_distribution: dict[str, list[int]] = defaultdict(list)
    division_wins: dict[str, int] = defaultdict(int)
    playoff_appearances: dict[str, int] = defaultdict(int)
    sb_appearances: dict[str, int] = defaultdict(int)

    rng = random.Random(42)  # deterministic — set None for fresh randomness each call
    hfa = elo_service.HOME_FIELD_ADVANTAGE
    elo_per_point = elo_service.ELO_PER_POINT

    for _sim in range(n_sims):
        # --- Correlated team strength (the variance fix) ----------------------
        # Draw a single season-long latent-strength offset per team and hold it
        # across that team's entire remaining slate. Because the offset persists,
        # a team that is "secretly good" in this trial wins across ALL its games,
        # which is what makes simulated win totals over-dispersed (realistically
        # wide) instead of an over-tight sum of independent coin flips. The draws
        # are mean-zero, so the central projection is unchanged — only the spread
        # widens.
        rating_sigma = _rating_sigma()
        offset = {t: rng.gauss(0.0, rating_sigma) for t in ratings}
        sim_wins: dict[str, int] = {t: banked_wins.get(t, 0) for t in ratings}
        sim_pd: dict[str, float] = {t: banked_pd.get(t, 0.0) for t in ratings}

        for game in pending:
            h, a = game["home"], game["away"]
            rh = ratings.get(h, elo_service.INITIAL_RATING) + offset.get(h, 0.0)
            ra = ratings.get(a, elo_service.INITIAL_RATING) + offset.get(a, 0.0)
            diff = rh - ra + (0.0 if game["neutral"] else hfa)
            expected_margin = diff / elo_per_point
            # Simulate an actual margin (not just W/L) so point differential is
            # available for tiebreakers and the win prob is consistent with the
            # game-level distribution model.
            margin = rng.gauss(expected_margin, GAME_SIGMA)
            if margin >= 0:
                sim_wins[h] = sim_wins.get(h, 0) + 1
            else:
                sim_wins[a] = sim_wins.get(a, 0) + 1
            sim_pd[h] = sim_pd.get(h, 0.0) + margin
            sim_pd[a] = sim_pd.get(a, 0.0) - margin

        # Division winners + playoff seeds. Ties broken by point differential,
        # then a coin flip (previously a pure coin flip).
        by_div: dict[tuple, list[tuple[str, int, float]]] = defaultdict(list)
        for team, wins in sim_wins.items():
            div = DIVISIONS.get(team)
            if div:
                by_div[div].append((team, wins, sim_pd.get(team, 0.0)))

        conf_seeds: dict[str, list[tuple[str, int, float]]] = defaultdict(list)
        for (conf, _division), teams in by_div.items():
            winner = max(teams, key=lambda t: (t[1], t[2], rng.random()))
            division_wins[winner[0]] += 1
            conf_seeds[conf].append(winner)

        # Wildcards (3 per conf): top 3 non-division-winners by wins, then PD.
        for conf, seeds in conf_seeds.items():
            seed_team_ids = {s[0] for s in seeds}
            others = [
                (team, sim_wins[team], sim_pd.get(team, 0.0))
                for team in sim_wins
                if DIVISIONS.get(team, (None,))[0] == conf and team not in seed_team_ids
            ]
            others.sort(key=lambda t: (-t[1], -t[2], rng.random()))
            conf_seeds[conf] = seeds + others[:3]

        # Mark playoff appearances
        for conf, all_seven in conf_seeds.items():
            for team, *_rest in all_seven:
                playoff_appearances[team] += 1
            # Crude SB heuristic: best seed in conference (wins, then PD)
            best = max(all_seven, key=lambda t: (t[1], t[2], rng.random()))
            sb_appearances[best[0]] += 1

        for team, wins in sim_wins.items():
            win_distribution[team].append(wins)

    # Aggregate
    out: dict[str, Any] = {}
    for team in ratings:
        dist = sorted(win_distribution.get(team, []))
        if not dist:
            continue
        mean_wins = sum(dist) / len(dist)
        var = sum((x - mean_wins) ** 2 for x in dist) / len(dist)
        out[team] = {
            "mean_wins": round(mean_wins, 1),
            "std_wins": round(var ** 0.5, 2),   # spread of the win-total distribution
            "p5_wins": dist[int(0.05 * len(dist))],
            "median_wins": dist[len(dist) // 2],
            "p95_wins": dist[int(0.95 * len(dist)) - 1],
            "division_winner_pct": round(100 * division_wins[team] / n_sims, 1),
            "playoff_pct": round(100 * playoff_appearances[team] / n_sims, 1),
            "sb_appearance_pct": round(100 * sb_appearances[team] / n_sims, 1),
        }

    return {"season": season, "n_sims": n_sims, "teams": out}


async def team_season_outlook(db: Session, team_id: str, season: int | None = None) -> dict[str, Any]:
    season = season or current_or_upcoming_season()
    sim = await simulate_season(db, season)
    return {
        "team_id": team_id,
        "season": season,
        **sim["teams"].get(team_id, {}),
    }


async def team_remaining_schedule_predictions(
    db: Session, team_id: str, season: int | None = None,
) -> dict[str, Any]:
    """Predicted spread + win prob for every remaining game in the team's season.

    Returns a list ordered by week with cumulative-wins projection so the UI
    can chart the expected trajectory.
    """
    season = season or current_or_upcoming_season()
    sched = await _season_schedule(season, db=db)
    if sched is None:
        return {"team_id": team_id, "season": season, "games": []}

    team_games = sched[(sched["home_team"] == team_id) | (sched["away_team"] == team_id)]
    team_games = team_games.sort_values("week")

    ratings = elo_service.current_ratings(db, season=season) or elo_service.current_ratings(db)
    aggs = await analytics_service._team_pbp_aggregates(season, allow_live_fallback=False)
    if not aggs:
        aggs = await analytics_service._team_pbp_aggregates(season - 1, allow_live_fallback=False)
    from . import model_inputs_service

    aggs = model_inputs_service.adjusted_team_aggregates(db, season, aggs or {})

    out_games = []
    cumulative_expected_wins = 0.0
    banked_wins = 0
    for _, g in team_games.iterrows():
        h, a = g["home_team"], g["away_team"]
        if not h or not a:
            continue
        is_home = h == team_id
        opp = a if is_home else h
        hs, as_ = g.get("home_score"), g.get("away_score")
        played = pd.notna(hs) and pd.notna(as_)

        hr = ratings.get(h, elo_service.INITIAL_RATING)
        ar = ratings.get(a, elo_service.INITIAL_RATING)
        h_off = (aggs.get(h) or {}).get("points_per_game")
        a_off = (aggs.get(a) or {}).get("points_per_game")
        h_def = (aggs.get(h) or {}).get("points_allowed_per_game")
        a_def = (aggs.get(a) or {}).get("points_allowed_per_game")
        # Deliberately uncontexted: this projects a team's whole remaining
        # schedule, and this week's injury report says nothing about Week 14.
        # Per-week context here needs a durability model (who is back by then),
        # which is the season-context work, not this one.
        pred = predict_game(hr, ar, home_off_ppg=h_off, away_off_ppg=a_off,
                            home_def_ppg_allowed=h_def, away_def_ppg_allowed=a_def,
                            home_aggs=aggs.get(h), away_aggs=aggs.get(a))
        my_win_prob = pred["home_win_prob"] if is_home else pred["away_win_prob"]

        outcome: str | None = None
        if played:
            if (hs > as_ and is_home) or (as_ > hs and not is_home):
                outcome = "W"
                banked_wins += 1
            elif hs == as_:
                outcome = "T"
                banked_wins += 0.5
            else:
                outcome = "L"

        if not played:
            cumulative_expected_wins += my_win_prob
        out_games.append({
            "id": str(g.get("game_id") or ""),
            "week": _safe_int(g.get("week")),
            "gameday": str(g.get("gameday") or ""),
            "opponent": opp,
            "is_home": is_home,
            "played": played,
            "outcome": outcome,
            "my_score": _safe_int(hs if is_home else as_),
            "opp_score": _safe_int(as_ if is_home else hs),
            "win_prob": round(my_win_prob, 3),
            "predicted_spread_for_team": round(
                pred["predicted_spread"] if is_home else -pred["predicted_spread"], 1
            ),
            "predicted_total": pred["predicted_total"],
            "cumulative_projected_wins": round(banked_wins + cumulative_expected_wins, 2),
            "opp_elo": round(ar if is_home else hr, 0),
        })

    final_projected = banked_wins + cumulative_expected_wins
    return {
        "team_id": team_id,
        "season": season,
        "games": out_games,
        "banked_wins": banked_wins,
        "projected_remaining_wins": round(cumulative_expected_wins, 2),
        "projected_total_wins": round(final_projected, 2),
    }


async def projected_standings(db: Session, season: int | None = None) -> dict[str, Any]:
    """Projected division standings: mean wins ordered per division."""
    season = season or current_or_upcoming_season()
    sim = await simulate_season(db, season)
    by_div: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for team_id, m in sim["teams"].items():
        div = DIVISIONS.get(team_id)
        if div is None:
            continue
        by_div[div].append({"team_id": team_id, **m})
    for k in by_div:
        by_div[k].sort(key=lambda t: -t["mean_wins"])
    return {
        "season": season,
        "divisions": [
            {"conference": conf, "division": div, "teams": teams}
            for (conf, div), teams in sorted(by_div.items())
        ],
    }


# ---- Helpers ---------------------------------------------------------------


def _safe_int(v) -> int | None:
    try:
        if pd.isna(v):
            return None
        return int(v)
    except (TypeError, ValueError):
        return None
