"""Betting analytics.

Two data sources combined:
- Historical ATS / O/U records → nfl-data-py schedules (closing `spread_line`
  and `total_line` come from nflverse and go back decades).
- Current-week market lines → the persisted `odds_lines` snapshot, which the
  scheduled job refreshes from The Odds API twice a day. We never call the
  paid API from this request path (free tier is 500 credits/mo); we read the
  DB snapshot so user traffic can't blow the budget.
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
from ..cache import cache
from ..logging_config import get_logger
from ..utils.seasons import current_or_upcoming_season, latest_completed_season
from ..utils.teams import canonical_team
from . import elo_service, odds_service, predictions_service

log = get_logger(__name__)
_nfl = NflDataPyAdapter()

CACHE_TTL_LONG = 60 * 60 * 12  # 12h for historical records
CACHE_TTL_SHORT = 60 * 5       # 5m: re-aggregation cache for the DB odds snapshot (no API impact)

EDGE_THRESHOLD = 2.0  # points difference flagged as a "value bet"
WIN_PROB_EDGE_THRESHOLD = 0.04  # 4 percentage points on moneyline implied prob


def _american_implied(price: int) -> float:
    """Convert American odds to implied win probability (no vig removal)."""
    if price < 0:
        return (-price) / ((-price) + 100.0)
    return 100.0 / (price + 100.0)


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


def _aggregate_market_from_events(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Map of 'Home Full Name|Away Full Name' -> consensus spread/total/ML."""
    out: dict[str, dict[str, Any]] = {}
    for ev in events:
        home = (ev.get("home_team") or "").strip()
        away = (ev.get("away_team") or "").strip()
        if not home or not away:
            continue
        spreads: list[float] = []
        totals: list[float] = []
        home_ml: list[float] = []
        for bm in ev.get("bookmakers", []):
            for mk in bm.get("markets", []):
                key = mk.get("key")
                if key == "spreads":
                    for outcome in mk.get("outcomes", []):
                        if outcome.get("name") == home and outcome.get("point") is not None:
                            spreads.append(float(outcome["point"]))
                elif key == "totals":
                    for outcome in mk.get("outcomes", []):
                        if outcome.get("name") == "Over" and outcome.get("point") is not None:
                            totals.append(float(outcome["point"]))
                elif key == "h2h":
                    for outcome in mk.get("outcomes", []):
                        price = outcome.get("price")
                        if outcome.get("name") == home and price is not None:
                            home_ml.append(_american_implied(int(price)))
        if spreads or totals or home_ml:
            out[f"{home}|{away}"] = {
                "market_spread_home": (float(np.median(spreads)) if spreads else None),
                "market_total": (float(np.median(totals)) if totals else None),
                "market_home_win_prob": (round(float(np.median(home_ml)), 3) if home_ml else None),
                "books": len(ev.get("bookmakers", [])),
            }
    return out


def _market_odds_from_db(db: Session) -> dict[str, dict[str, Any]]:
    """Build consensus lines from persisted odds_lines (Odds API refresh)."""
    lines = odds_service.list_odds(db, limit=4000)
    if not lines:
        return {}
    by_event: dict[str, dict[str, Any]] = {}
    for row in lines:
        home = (row.home_team or "").strip()
        away = (row.away_team or "").strip()
        if not home or not away:
            continue
        ev = by_event.setdefault(row.event_id, {
            "home_team": home,
            "away_team": away,
            "bookmakers": [],
        })
        bm_title = row.bookmaker or "unknown"
        bm = next((b for b in ev["bookmakers"] if b["title"] == bm_title), None)
        if bm is None:
            bm = {"title": bm_title, "markets": []}
            ev["bookmakers"].append(bm)
        mk = next((m for m in bm["markets"] if m["key"] == row.market), None)
        if mk is None:
            mk = {"key": row.market, "outcomes": []}
            bm["markets"].append(mk)
        outcome: dict[str, Any] = {"name": row.label}
        if row.price is not None:
            outcome["price"] = row.price
        if row.point is not None:
            outcome["point"] = row.point
        mk["outcomes"].append(outcome)
    return _aggregate_market_from_events(list(by_event.values()))


async def _current_market_odds(db: Session | None = None) -> dict[str, dict[str, Any]]:
    """Consensus spread/total/ML built from the persisted `odds_lines` snapshot.

    The snapshot is kept current by the scheduled odds job (twice daily). We do
    NOT call The Odds API here — doing so on the request path is what burned the
    500-credit/mo budget. The short L1 cache below only avoids re-aggregating the
    same DB rows on every request; it has no bearing on API usage.
    """
    key = "betting_current_market"
    if (v := cache.get(key)) is not None:
        return v

    out = _market_odds_from_db(db) if db is not None else {}
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


def _lookup_market_entry(
    market: dict[str, dict[str, Any]],
    name_map: dict[str, str],
    home_id: str,
    away_id: str,
) -> dict[str, Any] | None:
    for key, val in market.items():
        home_name, away_name = key.split("|", 1)
        if name_map.get(home_name.lower()) == home_id and name_map.get(away_name.lower()) == away_id:
            return val
    return None


async def matchup_market_context(
    db: Session,
    *,
    home_team_id: str,
    away_team_id: str,
    prediction: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Consensus market snapshot and model delta for a specific matchup."""
    market = await _current_market_odds(db)
    name_map = _full_name_to_id_map()
    entry = _lookup_market_entry(market, name_map, home_team_id, away_team_id)
    if not entry:
        return None

    out: dict[str, Any] = {"market": entry}
    if prediction:
        market_spread = entry.get("market_spread_home")
        market_total = entry.get("market_total")
        market_home_wp = entry.get("market_home_win_prob")
        pred_spread = prediction.get("predicted_spread")
        pred_total = prediction.get("predicted_total")
        pred_home_wp = prediction.get("home_win_prob")
        out["market_delta"] = {
            "spread": (
                round(float(market_spread) - float(pred_spread), 2)
                if market_spread is not None and pred_spread is not None
                else None
            ),
            "total": (
                round(float(pred_total) - float(market_total), 2)
                if market_total is not None and pred_total is not None
                else None
            ),
            "home_win_prob": (
                round(float(market_home_wp) - float(pred_home_wp), 3)
                if market_home_wp is not None and pred_home_wp is not None
                else None
            ),
        }
    return out


def _enrich_game_with_edge(
    g: dict[str, Any],
    market: dict[str, dict[str, Any]],
    name_map: dict[str, str],
) -> dict[str, Any]:
    """Attach market consensus + spread/total/win-prob edges vs our Elo prediction."""
    home_id = g["home_team_id"]
    away_id = g["away_team_id"]
    market_entry = _lookup_market_entry(market, name_map, home_id, away_id)

    edge_spread = None
    edge_total = None
    edge_win_prob = None
    recommendation = None
    pred = g.get("prediction") or {}

    if market_entry:
        ms = market_entry.get("market_spread_home")
        mt = market_entry.get("market_total")
        mw = market_entry.get("market_home_win_prob")
        our_s = pred.get("predicted_spread")
        our_t = pred.get("predicted_total")
        our_wp = pred.get("home_win_prob")
        if ms is not None and our_s is not None:
            edge_spread = round(ms - our_s, 1)  # positive = market more home-favoring
        if mt is not None and our_t is not None:
            edge_total = round(our_t - mt, 1)  # positive = we expect higher total
        if mw is not None and our_wp is not None:
            edge_win_prob = round(mw - our_wp, 3)  # positive = market higher on home

        if edge_spread is not None and abs(edge_spread) >= EDGE_THRESHOLD:
            team = home_id if edge_spread > 0 else away_id
            recommendation = f"{team} {('-' if edge_spread > 0 else '+')}{abs(edge_spread):.1f} spread edge"
        elif edge_win_prob is not None and abs(edge_win_prob) >= WIN_PROB_EDGE_THRESHOLD:
            team = home_id if edge_win_prob > 0 else away_id
            recommendation = f"{team} {abs(edge_win_prob) * 100:.0f}pt ML edge"

    return {
        **g,
        "market": market_entry,
        "edge_spread": edge_spread,
        "edge_total": edge_total,
        "edge_win_prob": edge_win_prob,
        "recommendation": recommendation,
    }


async def games_with_edge(
    db: Session, season: int | None = None, week: int | None = None,
) -> dict[str, Any]:
    """For each game in the upcoming week, attach market line + edge vs our prediction."""
    season = season or current_or_upcoming_season()
    base = await predictions_service.predict_week(db, season, week)
    if not base["games"]:
        return {"season": season, "week": base.get("week"), "games": []}

    market = await _current_market_odds(db)
    name_map = _full_name_to_id_map()
    enriched = [_enrich_game_with_edge(g, market, name_map) for g in base["games"]]
    return {"season": season, "week": base["week"], "games": enriched}


async def team_game_edge(
    db: Session, team_id: str, season: int | None = None,
) -> dict[str, Any]:
    """Edge metrics for the team's next unplayed game (full-season order, not just current week)."""
    tid = team_id.upper()
    season = season or current_or_upcoming_season()
    sched = await predictions_service.team_remaining_schedule_predictions(db, tid, season)
    upcoming = [g for g in sched.get("games", []) if not g.get("played")]
    if not upcoming:
        return {
            "team_id": tid,
            "season": season,
            "week": None,
            "games": [],
            "empty_reason": "season_complete",
        }

    next_row = upcoming[0]
    week = next_row.get("week")
    week_preds = await predictions_service.predict_week(db, season, week)
    game = next(
        (
            g for g in week_preds.get("games", [])
            if g["home_team_id"] == tid or g["away_team_id"] == tid
        ),
        None,
    )
    if game is None:
        is_home = next_row.get("is_home", True)
        opp = next_row["opponent"]
        home_id = tid if is_home else opp
        away_id = opp if is_home else tid
        wp = next_row.get("win_prob", 0.5)
        spread_for_team = next_row.get("predicted_spread_for_team", 0)
        home_spread = spread_for_team if is_home else -spread_for_team
        game = {
            "id": next_row.get("id", ""),
            "season": season,
            "week": week,
            "gameday": next_row.get("gameday", ""),
            "home_team_id": home_id,
            "away_team_id": away_id,
            "home_score": None,
            "away_score": None,
            "prediction": {
                "home_win_prob": wp if is_home else 1 - wp,
                "away_win_prob": 1 - wp if is_home else wp,
                "predicted_spread": home_spread,
                "predicted_total": next_row.get("predicted_total"),
            },
        }

    market = await _current_market_odds(db)
    name_map = _full_name_to_id_map()
    enriched = _enrich_game_with_edge(game, market, name_map)
    opp = next_row["opponent"]
    return {
        "team_id": tid,
        "season": season,
        "week": week,
        "opponent": opp,
        "games": [enriched],
    }


async def best_bets(db: Session, season: int | None = None) -> dict[str, Any]:
    """League-wide top-edge games for the current week."""
    out = await games_with_edge(db, season)
    rated = [g for g in out["games"] if g.get("edge_spread") is not None]
    rated.sort(key=lambda g: abs(g["edge_spread"]), reverse=True)
    return {"season": out.get("season"), "week": out.get("week"), "best_bets": rated[:8]}
