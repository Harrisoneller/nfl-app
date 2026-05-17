"""Player game + season stat predictions.

Pragmatic v1 model:
- **Baseline**: player's rolling 4-week per-game stat average.
- **Opponent adjustment**: shift baseline by ±0.10 × opponent defensive z-score
  (better defense → lower projection, weaker defense → higher).
- **Confidence band**: ±0.67 σ of the player's weekly stat volatility (~25th/75th).
- **Season projection**: current YTD + (games remaining × per-game prediction).

Future upgrades: position-specific defense (yards allowed to WRs vs RBs),
weather impact (already have the data), injury status (have status field).
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from ..adapters.data.nfl_data_py_adapter import NflDataPyAdapter
from ..cache import cache
from ..logging_config import get_logger
from ..models.player import Player
from ..utils.seasons import current_or_upcoming_season, latest_completed_season
from ..utils.teams import canonical_team
from . import analytics_service, elo_service, weather_service

log = get_logger(__name__)
_nfl = NflDataPyAdapter()

CACHE_TTL = 60 * 30  # 30 min

# Adjustment elasticity — how much one defensive σ changes the projection.
DEFENSE_ELASTICITY = 0.10

# Per-position stat kits to project. Keep close to what fans + DFS players care about.
POSITION_STATS: dict[str, list[str]] = {
    "QB": [
        "attempts", "completions", "passing_yards", "passing_tds", "interceptions",
        "carries", "rushing_yards", "rushing_tds", "fantasy_points_ppr",
    ],
    "RB": [
        "carries", "rushing_yards", "rushing_tds",
        "targets", "receptions", "receiving_yards", "receiving_tds", "fantasy_points_ppr",
    ],
    "WR": [
        "targets", "receptions", "receiving_yards", "receiving_tds", "fantasy_points_ppr",
    ],
    "TE": [
        "targets", "receptions", "receiving_yards", "receiving_tds", "fantasy_points_ppr",
    ],
}


async def _player_weekly_frame(season: int) -> pd.DataFrame | None:
    """Cached weekly DataFrame indexed by (player_id, week)."""
    key = f"player_weekly_indexed:{season}"
    if (v := cache.get(key)) is not None:
        return v
    df = await _nfl.weekly_df(season)
    if df is None or len(df) == 0:
        return None
    df = df.copy()
    # Normalize fantasy column
    if "fantasy_points_ppr" not in df.columns and "fantasy_points" in df.columns:
        df["fantasy_points_ppr"] = df["fantasy_points"]
    if "recent_team" in df.columns:
        df["recent_team"] = df["recent_team"].map(
            lambda x: canonical_team(x) if isinstance(x, str) else x
        )
    cache.set(key, df, CACHE_TTL)
    return df


def _rolling_stats(
    weekly: pd.DataFrame, player_id: str, stats: list[str], window: int = 4,
) -> dict[str, dict[str, float]]:
    """Returns { stat: { mean, std, n_games } } over the player's last `window` games."""
    sub = weekly[weekly["player_id"] == player_id] if "player_id" in weekly.columns else weekly.iloc[0:0]
    if len(sub) == 0:
        return {}
    sub = sub.sort_values("week").tail(window)
    out: dict[str, dict[str, float]] = {}
    for s in stats:
        if s not in sub.columns:
            continue
        vals = pd.to_numeric(sub[s], errors="coerce").dropna()
        if len(vals) == 0:
            continue
        out[s] = {
            "mean": float(vals.mean()),
            "std": float(vals.std() or 0.0),
            "n_games": int(len(vals)),
        }
    return out


def _season_totals(
    weekly: pd.DataFrame, player_id: str, stats: list[str],
) -> dict[str, float]:
    sub = weekly[weekly["player_id"] == player_id] if "player_id" in weekly.columns else weekly.iloc[0:0]
    if len(sub) == 0:
        return {s: 0.0 for s in stats}
    out: dict[str, float] = {}
    for s in stats:
        if s in sub.columns:
            out[s] = float(pd.to_numeric(sub[s], errors="coerce").fillna(0).sum())
        else:
            out[s] = 0.0
    out["games_played"] = int(sub["week"].nunique())
    return out


async def _opponent_def_z(season: int, opp_team_id: str | None) -> float:
    """Z-score of opponent's def_epa_per_play vs league. Higher z = WORSE defense."""
    if not opp_team_id:
        return 0.0
    aggs = await analytics_service._team_pbp_aggregates(season)  # noqa: SLF001 — internal but stable
    if not aggs:
        return 0.0
    epas = [t.get("def_epa_per_play") for t in aggs.values()]
    epas = [e for e in epas if isinstance(e, (int, float))]
    if not epas:
        return 0.0
    mean = float(np.mean(epas))
    std = float(np.std(epas)) or 1.0
    my = (aggs.get(opp_team_id) or {}).get("def_epa_per_play")
    if my is None:
        return 0.0
    # def_epa: higher = worse defense (allowing more EPA). Positive z = bad D = boost.
    return (float(my) - mean) / std


# ---- Public surface --------------------------------------------------------


async def player_game_predictions(
    db: Session, player_id: str, season: int | None = None,
) -> dict[str, Any]:
    """Predict stat lines for the player's remaining games in the season."""
    season = season or current_or_upcoming_season()
    cache_key = f"player_game_preds:{player_id}:{season}"
    if (cached := cache.get(cache_key)) is not None:
        return cached
    player: Player | None = db.get(Player, player_id)
    if player is None:
        return {"player_id": player_id, "error": "player not found"}
    pos = (player.position or "").upper()
    stats = POSITION_STATS.get(pos)
    if not stats:
        return {"player_id": player_id, "position": pos, "error": "unsupported position"}

    # Most recent COMPLETED season has full weekly data; in the offseason, use that
    # for rolling baseline but predict against the new upcoming schedule.
    weekly_season = season if season <= latest_completed_season() else latest_completed_season()
    weekly = await _player_weekly_frame(weekly_season)
    if weekly is None:
        return {"player_id": player_id, "error": "no weekly data available"}

    rolling = _rolling_stats(weekly, player_id, stats, window=4)
    if not rolling:
        return {
            "player_id": player_id, "position": pos, "team": player.team_id,
            "error": "no usage in recent games — no projections",
        }

    # Pull the team's remaining schedule from the current/upcoming season
    sched = await _nfl.schedules_df(season)
    if sched is None:
        return {"player_id": player_id, "error": "no schedule data"}
    sched = sched.copy()
    sched["home_team"] = sched["home_team"].map(lambda x: canonical_team(x) if isinstance(x, str) else x)
    sched["away_team"] = sched["away_team"].map(lambda x: canonical_team(x) if isinstance(x, str) else x)

    team_id = player.team_id
    if not team_id:
        return {"player_id": player_id, "error": "player has no team"}

    team_games = sched[(sched["home_team"] == team_id) | (sched["away_team"] == team_id)]
    remaining = team_games[team_games["home_score"].isna() | team_games["away_score"].isna()]
    remaining = remaining.sort_values("week").head(8)  # next 8 games

    # Player's current injury status (applies to all games unless we have per-week data)
    injury_status = (player.metadata_json or {}).get("injury_status")
    inj_mult = injury_multiplier(injury_status)

    # Batch weather lookup for the slate
    weather_inputs = []
    for _, g in remaining.iterrows():
        weather_inputs.append({
            "id": str(g.get("game_id") or ""),
            "home_team_id": g["home_team"],
            "gameday": str(g.get("gameday") or ""),
        })
    try:
        forecasts = await weather_service.forecasts_for_games(weather_inputs)
    except Exception:  # noqa: BLE001
        forecasts = {}

    games_out = []
    for _, g in remaining.iterrows():
        is_home = g["home_team"] == team_id
        opp = g["away_team"] if is_home else g["home_team"]
        z = await _opponent_def_z(weekly_season, opp)
        matchup_adj = 1.0 + DEFENSE_ELASTICITY * z

        game_id = str(g.get("game_id") or "")
        weather = forecasts.get(game_id, {"available": False})

        predicted: dict[str, dict[str, float | int]] = {}
        for s in stats:
            r = rolling.get(s)
            if r is None:
                continue
            base = r["mean"]
            std = r["std"]
            w_mult = weather_multiplier(weather, s)
            point = base * matchup_adj * w_mult * inj_mult
            # Variance unchanged by multipliers — they shift mean only.
            low = max(0.0, point - 0.67 * std)
            high = point + 0.67 * std
            predicted[s] = {
                "predicted": _round_for_stat(s, point),
                "low": _round_for_stat(s, low),
                "high": _round_for_stat(s, high),
                "n_games_baseline": r["n_games"],
            }
        games_out.append({
            "week": _safe_int(g.get("week")),
            "gameday": str(g.get("gameday") or ""),
            "home": g["home_team"],
            "away": g["away_team"],
            "opponent": opp,
            "is_home": bool(is_home),
            "opponent_def_z": round(z, 2),
            "matchup_grade": _matchup_grade(z),
            "weather": {
                "summary": weather_summary_blurb(weather),
                "is_indoor": bool(weather.get("is_indoor")),
                "available": bool(weather.get("available")),
            },
            "injury_status": injury_status,
            "injury_multiplier": round(inj_mult, 2),
            "predicted": predicted,
        })

    result = {
        "player_id": player_id,
        "name": player.full_name,
        "position": pos,
        "team": team_id,
        "season": season,
        "baseline_window": 4,
        "baseline_season": weekly_season,
        "games": games_out,
    }
    cache.set(cache_key, result, CACHE_TTL)
    return result


async def player_season_projection(
    db: Session, player_id: str, season: int | None = None,
) -> dict[str, Any]:
    """Current YTD + projected remaining + final season totals with bands."""
    season = season or current_or_upcoming_season()
    cache_key = f"player_season_proj:{player_id}:{season}"
    if (cached := cache.get(cache_key)) is not None:
        return cached
    player: Player | None = db.get(Player, player_id)
    if player is None:
        return {"player_id": player_id, "error": "player not found"}
    pos = (player.position or "").upper()
    stats = POSITION_STATS.get(pos)
    if not stats:
        return {"player_id": player_id, "position": pos, "error": "unsupported position"}

    weekly_season = season if season <= latest_completed_season() else latest_completed_season()
    weekly = await _player_weekly_frame(weekly_season)
    if weekly is None:
        return {"player_id": player_id, "error": "no weekly data available"}

    ytd = _season_totals(weekly, player_id, stats)
    rolling = _rolling_stats(weekly, player_id, stats, window=4)
    games_in_regular_season = 17

    # If we're in-season, the YTD comes from `season`; if offseason, treat YTD as 0 for `season`.
    if season > latest_completed_season():
        ytd_in_target_season = {s: 0.0 for s in stats}
        ytd_in_target_season["games_played"] = 0
        games_remaining = games_in_regular_season
    else:
        ytd_in_target_season = ytd
        games_remaining = max(0, games_in_regular_season - int(ytd.get("games_played", 0)))

    out_stats: dict[str, dict[str, float]] = {}
    for s in stats:
        r = rolling.get(s)
        per_game = r["mean"] if r else 0.0
        std = r["std"] if r else 0.0
        proj_rem = per_game * games_remaining
        ytd_v = float(ytd_in_target_season.get(s, 0.0))
        # Variance grows with games (assume independence — conservative)
        season_std = std * (games_remaining ** 0.5) if games_remaining else 0.0
        out_stats[s] = {
            "ytd": _round_for_stat(s, ytd_v),
            "per_game_pace": _round_for_stat(s, per_game),
            "projected_remaining": _round_for_stat(s, proj_rem),
            "projected_final": _round_for_stat(s, ytd_v + proj_rem),
            "low_final": _round_for_stat(s, ytd_v + max(0.0, proj_rem - 0.67 * season_std)),
            "high_final": _round_for_stat(s, ytd_v + proj_rem + 0.67 * season_std),
        }
    result = {
        "player_id": player_id,
        "name": player.full_name,
        "position": pos,
        "team": player.team_id,
        "season": season,
        "games_played": int(ytd_in_target_season.get("games_played", 0)),
        "games_remaining": games_remaining,
        "baseline_source_season": weekly_season,
        "stats": out_stats,
    }
    cache.set(cache_key, result, CACHE_TTL)
    return result


def _round_for_stat(stat: str, v: float) -> float:
    if v is None:
        return 0.0
    # Integers for counting stats, 1dp for fantasy points
    integer_stats = {
        "attempts", "completions", "passing_tds", "interceptions",
        "carries", "rushing_tds",
        "targets", "receptions", "receiving_tds",
    }
    if stat in integer_stats:
        return round(v, 0)
    return round(v, 1)


def _matchup_grade(z: float) -> str:
    """+z = bad defense = good matchup."""
    if z >= 1.0: return "A"
    if z >= 0.5: return "B"
    if z >= -0.5: return "C"
    if z >= -1.0: return "D"
    return "F"


# ---- Weather + injury multipliers -----------------------------------------

# Stat classes for adjustment routing.
_PASSING = {"attempts", "completions", "passing_yards", "passing_tds", "interceptions"}
_RUSHING = {"carries", "rushing_yards", "rushing_tds"}
_RECEIVING = {"targets", "receptions", "receiving_yards", "receiving_tds"}
_FANTASY = {"fantasy_points_ppr"}


def weather_multiplier(weather: dict | None, stat: str) -> float:
    """Returns a multiplier (1.0 = no adjustment) for the given stat given the forecast."""
    if not weather or not weather.get("available") or weather.get("is_indoor"):
        return 1.0
    wind = float(weather.get("wind_mph") or 0)
    precip = float(weather.get("precipitation_in") or 0)
    temp = weather.get("temperature_f")
    temp = float(temp) if temp is not None else 65.0

    mult = 1.0
    if stat in _PASSING:
        # Wind hurts passing the most; precip + cold compound it slightly.
        if wind >= 25:   mult *= 0.85
        elif wind >= 15: mult *= 0.92
        if precip >= 0.4: mult *= 0.85
        elif precip >= 0.15: mult *= 0.93
        if temp <= 25:   mult *= 0.95
    elif stat in _RUSHING:
        # Bad weather slightly boosts ground game share, but absolute yards stay flat.
        if wind >= 20 or precip >= 0.4:
            mult *= 1.04
    elif stat in _RECEIVING:
        # Receivers ride passing-game volatility.
        if wind >= 25:   mult *= 0.88
        elif wind >= 15: mult *= 0.94
        if precip >= 0.4: mult *= 0.88
        elif precip >= 0.15: mult *= 0.95
    elif stat in _FANTASY:
        # Composite — average of pass + rush + recv adjustments
        mult *= (weather_multiplier(weather, "passing_yards") * 0.4
                 + weather_multiplier(weather, "rushing_yards") * 0.25
                 + weather_multiplier(weather, "receiving_yards") * 0.35)
    return mult


def injury_multiplier(injury_status: str | None) -> float:
    """Status comes from Sleeper metadata."""
    if not injury_status:
        return 1.0
    s = injury_status.strip().upper()
    if s in ("OUT", "IR", "PUP", "NFI", "SUSPENDED"):
        return 0.0
    if s == "DOUBTFUL":
        return 0.3
    if s == "QUESTIONABLE":
        return 0.85
    if s in ("PROBABLE", "ACTIVE", "HEALTHY"):
        return 1.0
    return 1.0


def weather_summary_blurb(weather: dict | None) -> str | None:
    """One-line, fan-readable description for the UI."""
    if not weather or not weather.get("available"):
        return None
    if weather.get("is_indoor"):
        return "Indoor — no weather impact"
    parts: list[str] = []
    temp = weather.get("temperature_f")
    if temp is not None:
        parts.append(f"{int(temp)}°F")
    wind = weather.get("wind_mph")
    if wind is not None:
        parts.append(f"{int(wind)}mph wind")
    precip = weather.get("precipitation_in") or 0
    if precip >= 0.15:
        parts.append(f"{precip:.2f}in precip")
    summary = weather.get("summary") or ""
    if summary and summary != "—":
        parts.append(summary)
    return " · ".join(parts) if parts else None


def _safe_int(v) -> int | None:
    try:
        return int(v) if pd.notna(v) else None
    except (TypeError, ValueError):
        return None
