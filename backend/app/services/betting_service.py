"""Betting analytics.

Two data sources combined:
- Historical ATS / O/U records → nfl-data-py schedules (closing `spread_line`
  and `total_line` come from nflverse and go back decades).
- Current-week market lines → The Odds API (free tier).
- "Edge" = our predicted spread vs market spread. >|2.0| = notable.

ATS convention: `spread_line` from nflverse is HOME perspective (negative =
home favored). A team COVERS when their actual margin beats the spread.

  home covered  iff  home_margin > spread_line          (e.g. -7 spread, won by 10 → covered)
  away covered  iff  -(home_margin) > -(spread_line) iff away_margin > -spread_line
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from ..adapters.data.nfl_data_py_adapter import NflDataPyAdapter
from ..adapters.data.odds_api import TheOddsApiAdapter
from ..cache import cache
from ..logging_config import get_logger
from ..utils.seasons import latest_completed_season
from ..utils.teams import canonical_team
from . import elo_service, predictions_service

log = get_logger(__name__)
_nfl = NflDataPyAdapter()

CACHE_TTL_LONG = 60 * 60 * 12  # 12h for historical records
CACHE_TTL_SHORT = 60 * 5       # 5m for live market data

EDGE_THRESHOLD = 2.0  # points difference flagged as a "value bet"


# ---- Historical (nfl-data-py) -------------------------------------------- #


async def _completed_games_with_lines(seasons: list[int]) -> pd.DataFrame:
    """Concatenated schedule frames for the seasons, filtered to games with final
    scores AND closing lines."""
    key = f"completed_games_with_lines:{','.join(str(s) for s in seasons)}"
    if (v := cache.get(key)) is not None:
        return v
    frames = []
    for s in seasons:
        df = await _nfl.schedules_df(s)
        if df is None or len(df) == 0:
            continue
        df = df.copy()
        df["home_team"] = df["home_team"].map(
            lambda x: canonical_team(x) if isinstance(x, str) else x
        )
        df["away_team"] = df["away_team"].map(
            lambda x: canonical_team(x) if isinstance(x, str) else x
        )
        # Keep finals with closing lines available
        df = df.dropna(subset=["home_score", "away_score"])
        if "spread_line" in df.columns:
            df = df.dropna(subset=["spread_line"])
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    cache.set(key, out, CACHE_TTL_LONG)
    return out


def _record_from_subset(team_id: str, sub: pd.DataFrame) -> dict[str, Any]:
    """Compute ATS / O/U / W-L for one team across the given games."""
    if len(sub) == 0:
        return _empty_record()

    home = sub[sub["home_team"] == team_id].copy()
    away = sub[sub["away_team"] == team_id].copy()

    # Straight up
    home["su_win"] = home["home_score"] > home["away_score"]
    away["su_win"] = away["away_score"] > away["home_score"]
    su_wins = int(home["su_win"].sum() + away["su_win"].sum())
    su_losses = len(sub) - su_wins
    pushes = 0

    # ATS — spread_line is home perspective (negative = home favored)
    home["covered"] = (home["home_score"] - home["away_score"]) > home["spread_line"]
    home["push"] = (home["home_score"] - home["away_score"]) == home["spread_line"]
    away["covered"] = (away["away_score"] - away["home_score"]) > -away["spread_line"]
    away["push"] = (away["away_score"] - away["home_score"]) == -away["spread_line"]
    ats_wins = int(home["covered"].sum() + away["covered"].sum())
    ats_pushes = int(home["push"].sum() + away["push"].sum())
    ats_losses = len(sub) - ats_wins - ats_pushes

    # O/U
    has_total = "total_line" in sub.columns and not sub["total_line"].isna().all()
    over_wins = under_wins = ou_pushes = 0
    if has_total:
        sub2 = sub.dropna(subset=["total_line"])
        actual_total = sub2["home_score"] + sub2["away_score"]
        over_wins = int((actual_total > sub2["total_line"]).sum())
        under_wins = int((actual_total < sub2["total_line"]).sum())
        ou_pushes = int((actual_total == sub2["total_line"]).sum())

    # Splits: as favorite / underdog
    home["is_fav"] = home["spread_line"] < 0
    away["is_fav"] = away["spread_line"] > 0  # away favored when spread_line > 0
    fav_games = pd.concat([home[home["is_fav"]], away[away["is_fav"]]])
    dog_games = pd.concat([home[~home["is_fav"]], away[~away["is_fav"]]])

    return {
        "games": len(sub),
        "su": {"wins": su_wins, "losses": su_losses, "ties": pushes},
        "ats": {"wins": ats_wins, "losses": ats_losses, "pushes": ats_pushes,
                "win_pct": _safe_pct(ats_wins, ats_wins + ats_losses)},
        "ou": {"overs": over_wins, "unders": under_wins, "pushes": ou_pushes,
               "over_pct": _safe_pct(over_wins, over_wins + under_wins)},
        "as_favorite": _su_record(fav_games, team_id),
        "as_underdog": _su_record(dog_games, team_id),
        "home_split": _su_record(home, team_id),
        "away_split": _su_record(away, team_id),
    }


def _su_record(sub: pd.DataFrame, team_id: str) -> dict[str, Any]:
    if len(sub) == 0:
        return {"games": 0, "wins": 0, "losses": 0, "win_pct": 0.0}
    home = sub[sub["home_team"] == team_id]
    away = sub[sub["away_team"] == team_id]
    wins = int((home["home_score"] > home["away_score"]).sum() +
               (away["away_score"] > away["home_score"]).sum())
    losses = len(sub) - wins
    return {"games": len(sub), "wins": wins, "losses": losses, "win_pct": _safe_pct(wins, wins + losses)}


def _empty_record() -> dict[str, Any]:
    return {
        "games": 0,
        "su": {"wins": 0, "losses": 0, "ties": 0},
        "ats": {"wins": 0, "losses": 0, "pushes": 0, "win_pct": 0.0},
        "ou": {"overs": 0, "unders": 0, "pushes": 0, "over_pct": 0.0},
        "as_favorite": {"games": 0, "wins": 0, "losses": 0, "win_pct": 0.0},
        "as_underdog": {"games": 0, "wins": 0, "losses": 0, "win_pct": 0.0},
        "home_split": {"games": 0, "wins": 0, "losses": 0, "win_pct": 0.0},
        "away_split": {"games": 0, "wins": 0, "losses": 0, "win_pct": 0.0},
    }


def _safe_pct(n: int, d: int) -> float:
    if d == 0:
        return 0.0
    return round(100 * n / d, 1)


async def team_betting_history(team_id: str, seasons: list[int] | None = None) -> dict[str, Any]:
    """Lifetime + recent-window betting records for one team."""
    tid = team_id.upper()
    if seasons is None:
        latest = latest_completed_season()
        seasons = list(range(latest - 4, latest + 1))
    games = await _completed_games_with_lines(seasons)
    if len(games) == 0:
        return {"team_id": tid, "seasons": seasons, "lifetime": _empty_record(), "last20": _empty_record()}

    relevant = games[(games["home_team"] == tid) | (games["away_team"] == tid)]
    if "gameday" in relevant.columns:
        relevant = relevant.copy()
        relevant["gameday"] = pd.to_datetime(relevant["gameday"], errors="coerce")
        relevant = relevant.sort_values("gameday")

    last20 = relevant.tail(20)
    return {
        "team_id": tid,
        "seasons": seasons,
        "lifetime": _record_from_subset(tid, relevant),
        "last20": _record_from_subset(tid, last20),
    }


# ---- Current-week market edge -------------------------------------------- #


async def _current_market_odds() -> dict[str, dict[str, Any]]:
    """Map of normalized {home_team, away_team} -> {spread, total} from Odds API.

    Cached 5 min so it doesn't burn the free-tier quota.
    """
    key = "betting_current_market"
    if (v := cache.get(key)) is not None:
        return v
    adapter = TheOddsApiAdapter()
    try:
        events = await adapter.fetch_game_odds(markets=("spreads", "totals"), regions="us")
    finally:
        await adapter.aclose()
    out: dict[str, dict[str, Any]] = {}
    for ev in events:
        home = (ev.get("home_team") or "").strip()
        away = (ev.get("away_team") or "").strip()
        if not home or not away:
            continue
        # Collect spread and total across books, then take median (the consensus line)
        spreads: list[float] = []
        totals: list[float] = []
        for bm in ev.get("bookmakers", []):
            for mk in bm.get("markets", []):
                if mk.get("key") == "spreads":
                    for outcome in mk.get("outcomes", []):
                        if outcome.get("name") == home and outcome.get("point") is not None:
                            spreads.append(float(outcome["point"]))
                elif mk.get("key") == "totals":
                    for outcome in mk.get("outcomes", []):
                        if outcome.get("name") == "Over" and outcome.get("point") is not None:
                            totals.append(float(outcome["point"]))
        if spreads or totals:
            out[f"{home}|{away}"] = {
                "market_spread_home": (float(np.median(spreads)) if spreads else None),
                "market_total": (float(np.median(totals)) if totals else None),
                "books": len(ev.get("bookmakers", [])),
            }
    cache.set(key, out, CACHE_TTL_SHORT)
    return out


def _full_name_to_id_map() -> dict[str, str]:
    """The Odds API uses full team names; we canonicalize to 3-letter ids."""
    from ..models.seed import NFL_TEAMS
    out: dict[str, str] = {}
    for t in NFL_TEAMS:
        full = f"{t['market']} {t['name']}".strip()
        out[full.lower()] = t["id"]
        out[t["name"].lower()] = t["id"]
    return out


async def games_with_edge(
    db: Session, season: int | None = None, week: int | None = None,
) -> dict[str, Any]:
    """For each game in the upcoming week, attach market line + edge vs our prediction."""
    base = await predictions_service.predict_week(db, season or 0, week)
    if not base["games"]:
        return {"week": base.get("week"), "games": []}

    market = await _current_market_odds()
    name_map = _full_name_to_id_map()

    enriched = []
    for g in base["games"]:
        home_id = g["home_team_id"]
        away_id = g["away_team_id"]
        # Find the market entry by mapping full names back to ids
        market_entry = None
        for key, val in market.items():
            home_name, away_name = key.split("|", 1)
            if name_map.get(home_name.lower()) == home_id and name_map.get(away_name.lower()) == away_id:
                market_entry = val
                break
        edge_spread = None
        edge_total = None
        recommendation = None
        if market_entry:
            ms = market_entry["market_spread_home"]
            mt = market_entry["market_total"]
            our_s = g["prediction"]["predicted_spread"]
            our_t = g["prediction"]["predicted_total"]
            if ms is not None:
                edge_spread = round(ms - our_s, 1)  # positive = market is more home-favoring than us
            if mt is not None:
                edge_total = round(our_t - mt, 1)  # positive = we expect higher total than market

            if edge_spread is not None and abs(edge_spread) >= EDGE_THRESHOLD:
                # If our model says home should be more favored than market does, take home.
                team = home_id if edge_spread > 0 else away_id
                recommendation = f"{team} {('-' if edge_spread > 0 else '+')}{abs(edge_spread):.1f} edge"
        enriched.append({
            **g,
            "market": market_entry,
            "edge_spread": edge_spread,
            "edge_total": edge_total,
            "recommendation": recommendation,
        })
    return {"week": base["week"], "games": enriched}


async def best_bets(db: Session, season: int | None = None) -> dict[str, Any]:
    """League-wide top-edge games for the current week."""
    out = await games_with_edge(db, season)
    rated = [g for g in out["games"] if g.get("edge_spread") is not None]
    rated.sort(key=lambda g: abs(g["edge_spread"]), reverse=True)
    return {"week": out.get("week"), "best_bets": rated[:8]}
