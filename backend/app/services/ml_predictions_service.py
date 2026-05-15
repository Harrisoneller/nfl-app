"""ML point-spread model — XGBoost regression on rolling EPA + Elo + rest.

Trained on completed games from a range of historical seasons. Stored on
disk so we don't retrain every restart. Inference is fast (microseconds
per game).

Pragmatic v1 — features are deliberately simple and robust:
  - home_elo, away_elo
  - home_off_epa_rolling4, away_off_epa_rolling4
  - home_def_epa_rolling4, away_def_epa_rolling4
  - rest_days_home, rest_days_away
  - is_division_game (1/0)
  - week_number

Target: home_margin (home_score - away_score)

XGBoost is optional. If it can't be imported at runtime (e.g. CI), the
service returns predictions=None and the API surface degrades gracefully.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from ..adapters.data.nfl_data_py_adapter import NflDataPyAdapter
from ..cache import cache
from ..logging_config import get_logger
from ..models.seed import NFL_TEAMS
from ..utils.teams import canonical_team
from . import elo_service

log = get_logger(__name__)
_nfl = NflDataPyAdapter()

# Cache the *model* in-process; the trained model file persists on disk.
_MODEL_FILE = Path(os.environ.get("ML_MODEL_DIR", "/tmp")) / "nfl_margin_xgb.json"
_FEATURES = [
    "home_elo", "away_elo",
    "home_off_epa", "away_off_epa",
    "home_def_epa", "away_def_epa",
    "rest_days_home", "rest_days_away",
    "is_division_game", "week_number",
]

DIVISIONS = {t["id"]: (t["conference"], t["division"]) for t in NFL_TEAMS}

_xgb_available: bool | None = None


def _xgb():
    """Lazy import — keeps test/CI environments slim."""
    global _xgb_available
    if _xgb_available is False:
        return None
    try:
        import xgboost as xgb  # type: ignore
        _xgb_available = True
        return xgb
    except ImportError:
        _xgb_available = False
        log.warning("xgboost_not_installed_ml_disabled")
        return None


# ---- Feature construction -------------------------------------------------


async def _build_features_for_season(
    db: Session, season: int,
) -> pd.DataFrame | None:
    """Construct a per-game feature DataFrame for one season.

    Used for both training and inference. Rolling EPA is computed from the
    season's PBP up to (but not including) the game in question.
    """
    pbp = await _nfl.pbp_df(season)
    sched = await _nfl.schedules_df(season)
    if pbp is None or sched is None:
        return None

    pbp = pbp.copy()
    sched = sched.copy()
    for col in ("posteam", "defteam"):
        if col in pbp.columns:
            pbp[col] = pbp[col].map(lambda x: canonical_team(x) if isinstance(x, str) else x)
    sched["home_team"] = sched["home_team"].map(lambda x: canonical_team(x) if isinstance(x, str) else x)
    sched["away_team"] = sched["away_team"].map(lambda x: canonical_team(x) if isinstance(x, str) else x)
    pbp = pbp[pbp["play_type"].isin(["pass", "run"])]

    # Rolling 4-week offensive + defensive EPA per team
    off_epa = pbp.groupby(["posteam", "week"])["epa"].mean().reset_index()
    off_epa.columns = ["team", "week", "off_epa"]
    def_epa = pbp.groupby(["defteam", "week"])["epa"].mean().reset_index()
    def_epa.columns = ["team", "week", "def_epa"]
    team_weeks = off_epa.merge(def_epa, on=["team", "week"], how="outer")
    team_weeks = team_weeks.sort_values(["team", "week"])
    team_weeks["off_epa_roll4"] = (
        team_weeks.groupby("team")["off_epa"].rolling(4, min_periods=1).mean().reset_index(level=0, drop=True)
    )
    team_weeks["def_epa_roll4"] = (
        team_weeks.groupby("team")["def_epa"].rolling(4, min_periods=1).mean().reset_index(level=0, drop=True)
    )

    # Rest days from previous game per team
    sched = sched.dropna(subset=["gameday"]).copy()
    sched["gameday"] = pd.to_datetime(sched["gameday"], errors="coerce")
    long = pd.concat([
        sched[["game_id", "week", "gameday", "home_team"]].rename(columns={"home_team": "team"}).assign(side="home"),
        sched[["game_id", "week", "gameday", "away_team"]].rename(columns={"away_team": "team"}).assign(side="away"),
    ])
    long = long.sort_values(["team", "gameday"])
    long["rest_days"] = long.groupby("team")["gameday"].diff().dt.days.fillna(7).clip(lower=3, upper=21)
    rest_pivot = long.pivot_table(index="game_id", columns="side", values="rest_days").reset_index()
    rest_pivot.columns = ["game_id", "rest_days_away", "rest_days_home"]

    # Elo at start of each week
    elo_ratings = elo_service.current_ratings(db, season=season - 1) or {}
    if not elo_ratings:
        elo_ratings = {t["id"]: elo_service.INITIAL_RATING for t in NFL_TEAMS}

    # Build per-game frame
    rows = []
    for _, g in sched.iterrows():
        h, a = g["home_team"], g["away_team"]
        if not h or not a:
            continue
        week = int(g["week"]) if pd.notna(g["week"]) else 1
        # Use last week's rolling EPA as the "pre-game" feature
        target_week = max(week - 1, 1)
        ho = team_weeks[(team_weeks["team"] == h) & (team_weeks["week"] == target_week)]
        ao = team_weeks[(team_weeks["team"] == a) & (team_weeks["week"] == target_week)]
        row = {
            "game_id": g["game_id"],
            "season": season,
            "week_number": week,
            "home_team": h,
            "away_team": a,
            "home_elo": elo_ratings.get(h, elo_service.INITIAL_RATING),
            "away_elo": elo_ratings.get(a, elo_service.INITIAL_RATING),
            "home_off_epa": float(ho["off_epa_roll4"].iloc[0]) if len(ho) else 0.0,
            "away_off_epa": float(ao["off_epa_roll4"].iloc[0]) if len(ao) else 0.0,
            "home_def_epa": float(ho["def_epa_roll4"].iloc[0]) if len(ho) else 0.0,
            "away_def_epa": float(ao["def_epa_roll4"].iloc[0]) if len(ao) else 0.0,
            "is_division_game": 1 if DIVISIONS.get(h) == DIVISIONS.get(a) else 0,
            "home_score": _safe(g.get("home_score")),
            "away_score": _safe(g.get("away_score")),
        }
        rows.append(row)

    df = pd.DataFrame(rows)
    df = df.merge(rest_pivot, on="game_id", how="left")
    df["rest_days_home"] = df["rest_days_home"].fillna(7)
    df["rest_days_away"] = df["rest_days_away"].fillna(7)
    df["home_margin"] = df["home_score"] - df["away_score"]
    return df


def _safe(v) -> float | None:
    try:
        return float(v) if pd.notna(v) else None
    except (TypeError, ValueError):
        return None


# ---- Train ----------------------------------------------------------------


async def train(db: Session, seasons: list[int]) -> dict[str, Any]:
    """Fit XGBoost on the given seasons. Returns metrics."""
    xgb = _xgb()
    if xgb is None:
        return {"trained": False, "reason": "xgboost not installed"}

    frames = []
    for s in seasons:
        df = await _build_features_for_season(db, s)
        if df is not None:
            frames.append(df.dropna(subset=["home_margin"]))
    if not frames:
        return {"trained": False, "reason": "no training data"}
    big = pd.concat(frames, ignore_index=True)
    X = big[_FEATURES]
    y = big["home_margin"]

    model = xgb.XGBRegressor(
        n_estimators=300, max_depth=4, learning_rate=0.05,
        subsample=0.85, colsample_bytree=0.85, random_state=42,
    )
    model.fit(X, y)
    preds = model.predict(X)
    mae = float(((preds - y).abs()).mean())
    _MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
    model.save_model(str(_MODEL_FILE))
    cache.set("ml_model_loaded", model, 60 * 60 * 6)
    log.info("ml_trained", n_rows=len(big), mae=round(mae, 2), file=str(_MODEL_FILE))
    return {"trained": True, "rows": int(len(big)), "in_sample_mae": round(mae, 2)}


def _load_model():
    xgb = _xgb()
    if xgb is None:
        return None
    cached = cache.get("ml_model_loaded")
    if cached is not None:
        return cached
    if not _MODEL_FILE.exists():
        return None
    model = xgb.XGBRegressor()
    model.load_model(str(_MODEL_FILE))
    cache.set("ml_model_loaded", model, 60 * 60 * 6)
    return model


# ---- Inference ------------------------------------------------------------


async def predict_week_ml(db: Session, season: int, week: int) -> dict[str, Any]:
    model = _load_model()
    if model is None:
        return {"available": False}
    df = await _build_features_for_season(db, season)
    if df is None:
        return {"available": False}
    df = df[df["week_number"] == week]
    if len(df) == 0:
        return {"available": True, "games": []}
    X = df[_FEATURES]
    margins = model.predict(X)
    out = []
    for (_, g), margin in zip(df.iterrows(), margins):
        out.append({
            "game_id": g["game_id"],
            "home_team_id": g["home_team"],
            "away_team_id": g["away_team"],
            "predicted_spread": round(-float(margin), 1),  # negative = home favored
            "predicted_home_margin": round(float(margin), 1),
        })
    return {"available": True, "games": out}
