"""Point-in-time features for the market-blind NFL ML layer.

A result on date ``d`` may inform a target on date ``D`` only when
``d <= D - RESULT_LAG_DAYS``. That is the whole date rule. It is not "every
same-week game is excluded": a Monday game is eligible for Thursday of that
week. Week-1 self-inclusion and any game on ``D`` or ``D - 1`` are excluded.
Market columns are never copied onto the frame.
"""
from __future__ import annotations

import bisect
import math
from collections import defaultdict
from typing import Any

import pandas as pd

from ..models.seed import NFL_TEAMS
from .ml_blind_spec import (
    ELO_HOME_FIELD,
    ELO_INITIAL,
    ELO_K,
    ELO_SEASON_CARRY,
    EPA_HALF_LIFE_GAMES,
    EPA_WINDOW,
    FEATURE_COLUMNS,
    GRADED_GAME_TYPE,
    HISTORY_GAME_TYPES,
    OL_INJURY_EARLY,
    OL_INJURY_EARLY_WEEKS,
    OL_INJURY_LATER,
    REST_CLIP,
    REST_DEFAULT_DAYS,
    RESULT_LAG_DAYS,
    SHORT_WEEK_MAX_DAYS,
    assert_feature_allowlist,
    assert_no_market_columns,
)

DIVISIONS = {t["id"]: (t["conference"], t["division"]) for t in NFL_TEAMS}
_INDOOR_ROOFS = frozenset({"closed", "dome", "indoor"})


def model_matrix(frame: pd.DataFrame) -> pd.DataFrame:
    """Return the allowlisted matrix. Raises if the frame has a market column."""
    assert_no_market_columns(frame.columns)
    assert_feature_allowlist(FEATURE_COLUMNS)
    missing = [col for col in FEATURE_COLUMNS if col not in frame.columns]
    if missing:
        raise ValueError(f"missing features: {missing}")
    return frame.loc[:, list(FEATURE_COLUMNS)]


def team_game_epa(pbp: pd.DataFrame | None, schedule: pd.DataFrame) -> pd.DataFrame:
    """Mean pass/run EPA for and against, one row per team-game.

    Preseason plays are dropped when ``season_type`` is present. The gameday
    comes from the schedule. Games the schedule marks PRE are dropped.
    """
    empty = pd.DataFrame(columns=["game_id", "gameday", "team", "off_epa", "def_epa"])
    if pbp is None or len(pbp) == 0 or "epa" not in pbp.columns:
        return empty
    df = pbp.copy()
    if "season_type" in df.columns:
        kind = df["season_type"].astype(str).str.upper()
        df = df[kind.isin(HISTORY_GAME_TYPES)]
    if "play_type" in df.columns:
        df = df[df["play_type"].isin(["pass", "run"])]
    df = df[df["posteam"].notna() & df["defteam"].notna() & df["epa"].notna()]
    if len(df) == 0 or "game_id" not in df.columns:
        return empty
    off = df.groupby(["game_id", "posteam"], sort=False)["epa"].mean().reset_index()
    off.columns = ["game_id", "team", "off_epa"]
    deff = df.groupby(["game_id", "defteam"], sort=False)["epa"].mean().reset_index()
    deff.columns = ["game_id", "team", "def_epa"]
    merged = off.merge(deff, on=["game_id", "team"], how="outer")
    dates = schedule[["game_id", "gameday"]].drop_duplicates("game_id")
    merged = merged.merge(dates, on="game_id", how="left")
    if "game_type" in schedule.columns:
        pre = set(
            schedule.loc[
                schedule["game_type"].astype(str).str.upper() == "PRE", "game_id"
            ].astype(str)
        )
        merged = merged[~merged["game_id"].astype(str).isin(pre)]
    merged["gameday"] = pd.to_datetime(merged["gameday"], errors="coerce").dt.normalize()
    return merged.dropna(subset=["gameday", "team"])


def build_market_blind_frame(
    schedule: pd.DataFrame,
    team_games: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """One row per REG/POST game. Result features use only the T−24h cutoff."""
    if schedule is None or len(schedule) == 0:
        return _empty_frame()
    hist = _history_schedule(schedule)
    if len(hist) == 0:
        return _empty_frame()
    games = team_games if team_games is not None else pd.DataFrame()
    by_team = _epa_index(games)
    schedule_dates = _team_dates(hist)
    elo_games = [
        g for g in hist.itertuples(index=False)
        if _finite(getattr(g, "home_score", None)) and _finite(getattr(g, "away_score", None))
    ]
    ratings: dict[str, float] = defaultdict(lambda: ELO_INITIAL)
    elo_season: int | None = None
    elo_i = 0
    rows: list[dict[str, Any]] = []
    for game in hist.itertuples(index=False):
        target = pd.Timestamp(game.gameday)
        cutoff = target - pd.Timedelta(days=RESULT_LAG_DAYS)
        while elo_i < len(elo_games) and pd.Timestamp(elo_games[elo_i].gameday) <= cutoff:
            ingested = elo_games[elo_i]
            elo_season = _advance_elo_season(ratings, elo_season, int(ingested.season))
            _apply_elo_result(ratings, ingested)
            elo_i += 1
        # Carry into the target season before the row is read. The opener has
        # no new-season result to ingest, so the carry cannot wait for one.
        elo_season = _advance_elo_season(ratings, elo_season, int(game.season))
        home = str(game.home_team)
        away = str(game.away_team)
        home_epa = _epa_pair(by_team.get(home, ()), cutoff)
        away_epa = _epa_pair(by_team.get(away, ()), cutoff)
        home_rest = _rest_days(schedule_dates.get(home, ()), target)
        away_rest = _rest_days(schedule_dates.get(away, ()), target)
        week = int(game.week)
        roof = str(getattr(game, "roof", "") or "").strip().lower()
        home_qb, home_cont = _continuity(home_epa[0], home_epa[2])
        away_qb, away_cont = _continuity(away_epa[0], away_epa[2])
        ol = OL_INJURY_EARLY if week <= OL_INJURY_EARLY_WEEKS else OL_INJURY_LATER
        home_score = _num(getattr(game, "home_score", None))
        away_score = _num(getattr(game, "away_score", None))
        margin = None if home_score is None or away_score is None else home_score - away_score
        row = {
            "game_id": str(game.game_id),
            "season": int(game.season),
            "week_number": week,
            "gameday": target,
            "game_type": str(getattr(game, "game_type", GRADED_GAME_TYPE) or GRADED_GAME_TYPE).upper(),
            "home_team": home,
            "away_team": away,
            "home_score": home_score,
            "away_score": away_score,
            "home_margin": margin,
            "home_win": None if margin is None else int(margin > 0),
            "home_elo": float(ratings[home]),
            "away_elo": float(ratings[away]),
            "home_off_epa": home_epa[0],
            "away_off_epa": away_epa[0],
            "home_def_epa": home_epa[1],
            "away_def_epa": away_epa[1],
            "home_off_epa_recency": home_epa[2],
            "away_off_epa_recency": away_epa[2],
            "home_def_epa_recency": home_epa[3],
            "away_def_epa_recency": away_epa[3],
            "rest_days_home": home_rest,
            "rest_days_away": away_rest,
            "short_week_home": int(home_rest <= SHORT_WEEK_MAX_DAYS),
            "short_week_away": int(away_rest <= SHORT_WEEK_MAX_DAYS),
            "away_travel_proxy": _travel(home, away),
            "weather_class_code": 0 if roof in _INDOOR_ROOFS else 1,
            "weather_is_bad": 0,
            "qb_change_proxy_home": home_qb,
            "qb_change_proxy_away": away_qb,
            "offense_continuity_home": home_cont,
            "offense_continuity_away": away_cont,
            "ol_injury_proxy_home": ol,
            "ol_injury_proxy_away": ol,
            "is_division_game": int(DIVISIONS.get(home) == DIVISIONS.get(away) and home in DIVISIONS),
        }
        rows.append(row)
    frame = pd.DataFrame(rows)
    assert_no_market_columns(frame.columns)
    model_matrix(frame)
    return frame


def _empty_frame() -> pd.DataFrame:
    columns = [
        "game_id", "season", "week_number", "gameday", "game_type",
        "home_team", "away_team", "home_score", "away_score", "home_margin", "home_win",
        *FEATURE_COLUMNS,
    ]
    return pd.DataFrame(columns=columns)


def _history_schedule(schedule: pd.DataFrame) -> pd.DataFrame:
    required = {"game_id", "season", "week", "gameday", "home_team", "away_team"}
    missing = required - set(schedule.columns)
    if missing:
        raise ValueError(f"schedule is missing columns: {sorted(missing)}")
    hist = schedule.dropna(subset=["home_team", "away_team", "gameday", "week"]).copy()
    hist["gameday"] = pd.to_datetime(hist["gameday"], errors="coerce").dt.normalize()
    hist = hist.dropna(subset=["gameday"])
    hist["week"] = hist["week"].astype(int)
    hist["season"] = hist["season"].astype(int)
    hist["game_id"] = hist["game_id"].astype(str)
    if "game_type" in hist.columns:
        kind = hist["game_type"].astype(str).str.upper()
        hist = hist[kind.isin(HISTORY_GAME_TYPES)]
    hist = hist.drop_duplicates("game_id").sort_values(["gameday", "game_id"])
    return hist.reset_index(drop=True)


def _epa_index(team_games: pd.DataFrame) -> dict[str, list[tuple]]:
    if team_games is None or len(team_games) == 0:
        return {}
    games = team_games.copy()
    if "game_type" in games.columns:
        games = games[games["game_type"].astype(str).str.upper().isin(HISTORY_GAME_TYPES)]
    games["gameday"] = pd.to_datetime(games["gameday"], errors="coerce").dt.normalize()
    games = games.dropna(subset=["gameday", "team"])
    indexed: dict[str, list[tuple]] = defaultdict(list)
    for rec in games.itertuples(index=False):
        indexed[str(rec.team)].append((
            pd.Timestamp(rec.gameday),
            _num(getattr(rec, "off_epa", 0.0)) or 0.0,
            _num(getattr(rec, "def_epa", 0.0)) or 0.0,
            str(getattr(rec, "game_id", "")),
        ))
    for events in indexed.values():
        events.sort(key=lambda item: (item[0], item[3]))
    return indexed


def _team_dates(hist: pd.DataFrame) -> dict[str, list[pd.Timestamp]]:
    dates: dict[str, list[pd.Timestamp]] = defaultdict(list)
    for rec in hist.itertuples(index=False):
        day = pd.Timestamp(rec.gameday)
        dates[str(rec.home_team)].append(day)
        dates[str(rec.away_team)].append(day)
    for team, days in list(dates.items()):
        dates[team] = sorted(set(days))
    return dates


def _epa_pair(events: list[tuple] | tuple, cutoff: pd.Timestamp) -> tuple[float, float, float, float]:
    if not events:
        return (0.0, 0.0, 0.0, 0.0)
    dates = [event[0] for event in events]
    end = bisect.bisect_right(dates, cutoff)
    window = events[max(0, end - EPA_WINDOW):end]
    if not window:
        return (0.0, 0.0, 0.0, 0.0)
    offs = [float(event[1]) for event in window]
    defs = [float(event[2]) for event in window]
    ages = list(range(len(window) - 1, -1, -1))
    weights = [0.5 ** (age / EPA_HALF_LIFE_GAMES) for age in ages]
    scale = sum(weights) or 1.0

    def weighted(values: list[float]) -> float:
        return sum(value * weight for value, weight in zip(values, weights)) / scale

    return (
        sum(offs) / len(offs),
        sum(defs) / len(defs),
        weighted(offs),
        weighted(defs),
    )


def _rest_days(dates: list[pd.Timestamp] | tuple, target: pd.Timestamp) -> float:
    if not dates:
        return REST_DEFAULT_DAYS
    pos = bisect.bisect_left(dates, target)
    if pos == 0:
        return REST_DEFAULT_DAYS
    prior = dates[pos - 1]
    if prior >= target:
        return REST_DEFAULT_DAYS
    raw = float((target - prior).days)
    return float(min(REST_CLIP[1], max(REST_CLIP[0], raw)))


def _continuity(off_epa: float, recency: float) -> tuple[float, float]:
    gap = abs(off_epa - recency)
    return min(1.0, gap * 1.2), max(0.0, 1.0 - min(1.0, gap))


def _travel(home: str, away: str) -> float:
    home_div = DIVISIONS.get(home)
    away_div = DIVISIONS.get(away)
    if not home_div or not away_div:
        return 0.0
    if home_div[0] != away_div[0]:
        return 1.0
    if home_div[1] != away_div[1]:
        return 0.6
    return 0.2


def _advance_elo_season(ratings: dict[str, float], elo_season: int | None, season: int) -> int:
    """Regress stored ratings up to ``season`` before any row in that season is read."""
    if elo_season is None:
        return season
    if season < elo_season:
        raise ValueError(f"Elo season went backwards from {elo_season} to {season}")
    steps = season - elo_season
    for _ in range(steps):
        for team in list(ratings):
            ratings[team] = (
                ELO_SEASON_CARRY * ratings[team] + (1.0 - ELO_SEASON_CARRY) * ELO_INITIAL
            )
    return season


def _apply_elo_result(ratings: dict[str, float], game: Any) -> None:
    neutral = str(getattr(game, "location", "") or "") == "Neutral"
    margin = int(game.home_score) - int(game.away_score)
    home, away = str(game.home_team), str(game.away_team)
    ratings[home], ratings[away] = _update_elo(ratings[home], ratings[away], margin, neutral)


def assert_prefit_coverage(schedule: pd.DataFrame, team_games: pd.DataFrame) -> dict[str, int]:
    """Count REG 2012–2025 team-games that lack a cutoff-eligible EPA row.

    Raises unless 2011 history is present, every team id is known, and every
    counted team-game has EPA from a game on or before its date minus the lag.
    This does not score outcomes. Callers must not point it at a holdout file
    until QA has locked the commit.
    """
    known = set(DIVISIONS)
    hist = _history_schedule(schedule)
    games = team_games if team_games is not None else pd.DataFrame()
    if len(games) == 0 or "season" not in games.columns:
        raise ValueError("2011 EPA history is required")
    epa = games.copy()
    epa["gameday"] = pd.to_datetime(epa["gameday"], errors="coerce").dt.normalize()
    epa = epa.dropna(subset=["gameday", "team"])
    epa["season"] = epa["season"].astype(int)
    if not (epa["season"] == 2011).any():
        raise ValueError("2011 EPA history is required")
    if not (hist["season"] == 2011).any():
        raise ValueError("2011 schedule history is required")
    ids = set(hist["home_team"].astype(str)) | set(hist["away_team"].astype(str)) | set(epa["team"].astype(str))
    unknown = sorted(team for team in ids if team not in known)
    if unknown:
        raise ValueError(f"unknown team ids: {unknown}")
    by_team = _epa_index(epa)
    if "game_type" in hist.columns:
        reg_mask = hist["game_type"].astype(str).str.upper().eq("REG")
    else:
        reg_mask = pd.Series(True, index=hist.index)
    reg = hist[reg_mask & hist["season"].between(2012, 2025)]
    missing = 0
    counted = 0
    for game in reg.itertuples(index=False):
        cutoff = pd.Timestamp(game.gameday) - pd.Timedelta(days=RESULT_LAG_DAYS)
        for team in (str(game.home_team), str(game.away_team)):
            counted += 1
            dates = [event[0] for event in by_team.get(team, ())]
            if bisect.bisect_right(dates, cutoff) == 0:
                missing += 1
    if counted == 0:
        raise ValueError("no REG 2012-2025 team-games to check")
    if missing:
        raise ValueError(f"{missing} REG team-games in 2012-2025 have no eligible EPA history")
    return {
        "reg_team_games": counted,
        "missing_epa_history": 0,
        "unknown_ids": 0,
        "has_2011": 1,
    }


def _update_elo(home: float, away: float, margin: int, neutral: bool) -> tuple[float, float]:
    hfa = 0.0 if neutral else ELO_HOME_FIELD
    expected = 1.0 / (1.0 + 10 ** (-(home - away + hfa) / 400.0))
    actual = 1.0 if margin > 0 else (0.0 if margin < 0 else 0.5)
    diff = home - away + hfa
    if margin < 0:
        diff = -diff
    mov = math.log(max(abs(margin), 1) + 1) * (2.2 / (abs(diff) * 0.001 + 2.2))
    delta = ELO_K * mov * (actual - expected)
    return home + delta, away - delta


def _finite(value: Any) -> bool:
    try:
        return value is not None and not pd.isna(value)
    except TypeError:
        return False


def _num(value: Any) -> float | None:
    if not _finite(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
