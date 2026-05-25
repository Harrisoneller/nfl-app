"""Task-specific ML predictions with market-residual + calibration support."""
from __future__ import annotations

import json
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
from . import elo_service, feature_store_service
from . import prediction_dist, task_modeling_service

log = get_logger(__name__)
_nfl = NflDataPyAdapter()

_MODEL_DIR = Path(os.environ.get("ML_MODEL_DIR", "/tmp"))
_FEATURES = [
    "home_elo",
    "away_elo",
    "home_off_epa",
    "away_off_epa",
    "home_def_epa",
    "away_def_epa",
    "home_off_epa_recency",
    "away_off_epa_recency",
    "home_def_epa_recency",
    "away_def_epa_recency",
    "rest_days_home",
    "rest_days_away",
    "short_week_home",
    "short_week_away",
    "away_travel_proxy",
    "weather_class_code",
    "weather_is_bad",
    "qb_change_proxy_home",
    "qb_change_proxy_away",
    "offense_continuity_home",
    "offense_continuity_away",
    "ol_injury_proxy_home",
    "ol_injury_proxy_away",
    "is_division_game",
    "week_number",
]

DIVISIONS = {t["id"]: (t["conference"], t["division"]) for t in NFL_TEAMS}
TASK_CONFIGS = task_modeling_service.TASK_CONFIGS
DEFAULT_PRIMARY_TASK = task_modeling_service.TASK_SPREAD_EDGE

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


async def _build_features_for_season(db: Session, season: int) -> pd.DataFrame | None:
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
        home_off_epa = float(ho["off_epa_roll4"].iloc[0]) if len(ho) else 0.0
        away_off_epa = float(ao["off_epa_roll4"].iloc[0]) if len(ao) else 0.0
        home_def_epa = float(ho["def_epa_roll4"].iloc[0]) if len(ho) else 0.0
        away_def_epa = float(ao["def_epa_roll4"].iloc[0]) if len(ao) else 0.0
        home_off_prev = _team_prev_metric(team_weeks, h, target_week, "off_epa_roll4")
        away_off_prev = _team_prev_metric(team_weeks, a, target_week, "off_epa_roll4")
        home_def_prev = _team_prev_metric(team_weeks, h, target_week, "def_epa_roll4")
        away_def_prev = _team_prev_metric(team_weeks, a, target_week, "def_epa_roll4")

        weather_class = task_modeling_service.weather_class(
            roof=_safe_str(g.get("roof")),
            wind_mph=_safe(g.get("weather_wind_mph")),
            precip_in=_safe(g.get("weather_precipitation")),
            temperature_f=_safe(g.get("weather_temperature")),
        )
        weather_code = {"indoor": 0, "mild": 1, "cold": 2, "wet": 3, "windy": 4}.get(weather_class, 1)
        market_spread_home = _safe(g.get("spread_line"))
        market_baseline_margin = None if market_spread_home is None else -market_spread_home
        market_home_win_prob = _market_home_win_prob(g)
        travel_proxy = _travel_proxy(home_id=h, away_id=a)

        row = {
            "game_id": g["game_id"],
            "season": season,
            "week_number": week,
            "home_team": h,
            "away_team": a,
            "home_elo": elo_ratings.get(h, elo_service.INITIAL_RATING),
            "away_elo": elo_ratings.get(a, elo_service.INITIAL_RATING),
            "home_off_epa": home_off_epa,
            "away_off_epa": away_off_epa,
            "home_def_epa": home_def_epa,
            "away_def_epa": away_def_epa,
            "home_off_epa_recency": home_off_prev,
            "away_off_epa_recency": away_off_prev,
            "home_def_epa_recency": home_def_prev,
            "away_def_epa_recency": away_def_prev,
            "is_division_game": 1 if DIVISIONS.get(h) == DIVISIONS.get(a) else 0,
            "short_week_home": 0,
            "short_week_away": 0,
            "away_travel_proxy": travel_proxy,
            "weather_class_code": weather_code,
            "weather_is_bad": 1 if weather_class in {"wet", "windy", "cold"} else 0,
            "qb_change_proxy_home": min(1.0, abs(home_off_epa - home_off_prev) * 1.2),
            "qb_change_proxy_away": min(1.0, abs(away_off_epa - away_off_prev) * 1.2),
            "offense_continuity_home": max(0.0, 1.0 - min(1.0, abs(home_off_epa - home_off_prev))),
            "offense_continuity_away": max(0.0, 1.0 - min(1.0, abs(away_off_epa - away_off_prev))),
            # TODO: Replace with a true OL starter/injury feed when available.
            "ol_injury_proxy_home": 0.25 if week <= 2 else 0.1,
            "ol_injury_proxy_away": 0.25 if week <= 2 else 0.1,
            "home_score": _safe(g.get("home_score")),
            "away_score": _safe(g.get("away_score")),
            "market_spread_home": market_spread_home,
            "market_baseline_home_margin": market_baseline_margin,
            "market_home_win_prob": market_home_win_prob,
            "market_available": 1 if market_baseline_margin is not None or market_home_win_prob is not None else 0,
        }
        rows.append(row)

    df = pd.DataFrame(rows)
    df = df.merge(rest_pivot, on="game_id", how="left")
    df["rest_days_home"] = df["rest_days_home"].fillna(7)
    df["rest_days_away"] = df["rest_days_away"].fillna(7)
    df["short_week_home"] = (df["rest_days_home"] <= 6).astype(int)
    df["short_week_away"] = (df["rest_days_away"] <= 6).astype(int)
    df["market_home_win_prob"] = df["market_home_win_prob"].fillna(
        df["market_spread_home"].map(
            lambda x: prediction_dist.win_prob(-float(x), prediction_dist.NFL_MARGIN_SIGMA)
            if pd.notna(x)
            else 0.5
        )
    )
    df["home_margin"] = df["home_score"] - df["away_score"]
    df["home_win"] = (df["home_margin"] > 0).astype(int)
    return df


def _safe(v) -> float | None:
    try:
        return float(v) if pd.notna(v) else None
    except (TypeError, ValueError):
        return None


def _safe_str(v: Any) -> str | None:
    return str(v) if v is not None else None


def _team_prev_metric(team_weeks: pd.DataFrame, team: str, week: int, col: str) -> float:
    prior = team_weeks[(team_weeks["team"] == team) & (team_weeks["week"] <= week)].sort_values("week")
    if len(prior) == 0:
        return 0.0
    window = prior.tail(4).copy()
    current_index = int(week)
    weights = [
        task_modeling_service.exponential_recency_weight(
            current_index=current_index,
            sample_index=int(w),
            half_life=3.5,
        )
        for w in window["week"].tolist()
    ]
    values = [float(v) for v in window[col].fillna(0.0).tolist()]
    w_sum = sum(weights) or 1.0
    return float(sum(v * w for v, w in zip(values, weights)) / w_sum)


def _travel_proxy(*, home_id: str, away_id: str) -> float:
    home_div = DIVISIONS.get(home_id)
    away_div = DIVISIONS.get(away_id)
    if not home_div or not away_div:
        return 0.0
    if home_div[0] != away_div[0]:
        return 1.0
    if home_div[1] != away_div[1]:
        return 0.6
    return 0.2


def _market_home_win_prob(row: pd.Series) -> float | None:
    hm = _safe(row.get("home_moneyline"))
    am = _safe(row.get("away_moneyline"))
    if hm is None and am is None:
        return None
    # American odds to implied probability.
    def _implied(price: float) -> float:
        p = int(price)
        if p < 0:
            return (-p) / ((-p) + 100.0)
        return 100.0 / (p + 100.0)

    if hm is not None:
        return _implied(hm)
    away_p = _implied(am or 0.0)
    return max(0.01, min(0.99, 1.0 - away_p))


def _sample_weight(frame: pd.DataFrame) -> pd.Series:
    max_idx = int((frame["season"] * 100 + frame["week_number"]).max())
    recency = frame.apply(
        lambda r: task_modeling_service.exponential_recency_weight(
            current_index=max_idx,
            sample_index=int(r["season"] * 100 + r["week_number"]),
            half_life=6.0,
        ),
        axis=1,
    )
    regime_boost = (
        1.0
        + (0.15 * frame["short_week_home"].astype(float))
        + (0.10 * frame["short_week_away"].astype(float))
        + (0.08 * frame["weather_is_bad"].astype(float))
        + (0.10 * frame["qb_change_proxy_home"].astype(float))
        + (0.10 * frame["qb_change_proxy_away"].astype(float))
    )
    return recency * regime_boost


def _task_model_file(task: str) -> Path:
    return _MODEL_DIR / f"nfl_{task}_xgb.json"


def _task_calibration_file(task: str) -> Path:
    return _MODEL_DIR / f"nfl_{task}_calibration.json"


def _cached_model_key(task: str) -> str:
    return f"ml_model_loaded:{task}"


def _load_task_calibration(task: str) -> dict[str, Any] | None:
    f = _task_calibration_file(task)
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except Exception:  # noqa: BLE001
        return None


def _save_task_calibration(task: str, payload: dict[str, Any]) -> None:
    f = _task_calibration_file(task)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(payload))


# ---- Train ----------------------------------------------------------------


async def train(db: Session, seasons: list[int]) -> dict[str, Any]:
    """Fit task-specific residual models and calibration artifacts."""
    xgb = _xgb()
    if xgb is None:
        return {"trained": False, "reason": "xgboost not installed"}

    frames: list[pd.DataFrame] = []
    for s in seasons:
        df = await _build_features_for_season(db, s)
        if df is not None:
            frames.append(df.dropna(subset=["home_margin"]))
    if not frames:
        return {"trained": False, "reason": "no training data"}
    big = pd.concat(frames, ignore_index=True)
    weights = _sample_weight(big)
    results: dict[str, Any] = {}

    for task, cfg in TASK_CONFIGS.items():
        if cfg.is_scaffold:
            results[task] = {
                "trained": False,
                "status": "scaffold",
                "model_version": cfg.model_version,
                "feature_set_version": cfg.feature_set_version,
            }
            continue
        model = xgb.XGBRegressor(
            n_estimators=260,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.85,
            colsample_bytree=0.85,
            random_state=42,
        )
        X = big[_FEATURES]
        if task == task_modeling_service.TASK_SPREAD_EDGE:
            y = big["home_margin"] - big["market_baseline_home_margin"].fillna(0.0)
        else:
            y = big["home_win"] - big["market_home_win_prob"].fillna(0.5)
        model.fit(X, y, sample_weight=weights)
        preds = model.predict(X)
        resid_mae = float((preds - y).abs().mean())

        model_file = _task_model_file(task)
        model_file.parent.mkdir(parents=True, exist_ok=True)
        model.save_model(str(model_file))
        cache.set(_cached_model_key(task), model, 60 * 60 * 6)

        calibration_payload: dict[str, Any] | None = None
        if task == task_modeling_service.TASK_WIN_PROBABILITY:
            base_probs = big["market_home_win_prob"].fillna(0.5).tolist()
            raw_probs = [
                min(max(base + float(res), 1e-6), 1 - 1e-6)
                for base, res in zip(base_probs, preds.tolist())
            ]
            outcomes = big["home_win"].astype(int).tolist()
            previous = _load_task_calibration(task)
            platt = task_modeling_service.fit_platt_calibration(raw_probs=raw_probs, outcomes=outcomes)
            calibrated = [task_modeling_service.apply_platt_calibration(p, platt) for p in raw_probs]
            metrics = {
                "ece": round(task_modeling_service.expected_calibration_error(calibrated, outcomes), 6),
                "brier": round(task_modeling_service.brier_score(calibrated, outcomes), 6),
                "ece_raw": round(task_modeling_service.expected_calibration_error(raw_probs, outcomes), 6),
                "brier_raw": round(task_modeling_service.brier_score(raw_probs, outcomes), 6),
            }
            drift = task_modeling_service.calibration_drift(
                current_metrics={"ece": metrics["ece"], "brier": metrics["brier"]},
                prior_metrics=(previous or {}).get("metrics"),
            )
            calibration_payload = {
                **platt,
                "metrics": metrics,
                "drift": drift,
                "model_version": cfg.model_version,
            }
            _save_task_calibration(task, calibration_payload)

        feature_store_service.store_game_feature_frame(
            db,
            frame=big,
            season=int(big["season"].max()),
            model_version=cfg.model_version,
            feature_set_version=f"{cfg.feature_set_version}:multi-season",
            source=f"training:{task}",
        )
        results[task] = {
            "trained": True,
            "rows": int(len(big)),
            "residual_mae": round(resid_mae, 4),
            "model_version": cfg.model_version,
            "feature_set_version": cfg.feature_set_version,
            "calibration": calibration_payload,
            "residual_diagnostics": {
                "market_available_pct": round(float(big["market_available"].mean()), 4),
                "target_kind": cfg.target_kind,
            },
        }

    primary = results.get(DEFAULT_PRIMARY_TASK, {})
    return {
        "trained": bool(primary.get("trained")),
        "rows": int(len(big)),
        "in_sample_mae": round(float(primary.get("residual_mae", 0.0)), 4),
        "task_models": results,
    }


def _load_model(task: str):
    xgb = _xgb()
    if xgb is None:
        return None
    cached = cache.get(_cached_model_key(task))
    if cached is not None:
        return cached
    model_file = _task_model_file(task)
    if not model_file.exists():
        return None
    model = xgb.XGBRegressor()
    model.load_model(str(model_file))
    cache.set(_cached_model_key(task), model, 60 * 60 * 6)
    return model


# ---- Inference ------------------------------------------------------------


async def predict_week_ml(db: Session, season: int, week: int) -> dict[str, Any]:
    spread_model = _load_model(task_modeling_service.TASK_SPREAD_EDGE)
    win_model = _load_model(task_modeling_service.TASK_WIN_PROBABILITY)
    if spread_model is None and win_model is None:
        return {"available": False}
    df = await _build_features_for_season(db, season)
    if df is None:
        return {"available": False}
    weekly = df[df["week_number"] == week]
    feature_store_service.store_game_feature_frame(
        db,
        frame=weekly,
        season=season,
        model_version=TASK_CONFIGS[task_modeling_service.TASK_SPREAD_EDGE].model_version,
        feature_set_version=f"{TASK_CONFIGS[task_modeling_service.TASK_SPREAD_EDGE].feature_set_version}:{season}",
        source="inference",
    )
    if len(weekly) == 0:
        return {"available": True, "games": []}

    X = weekly[_FEATURES]
    spread_residuals = spread_model.predict(X) if spread_model is not None else [0.0] * len(weekly)
    win_residuals = win_model.predict(X) if win_model is not None else [0.0] * len(weekly)
    win_calibration = _load_task_calibration(task_modeling_service.TASK_WIN_PROBABILITY)
    out = []
    for idx, (_, g) in enumerate(weekly.iterrows()):
        baseline_margin = _safe(g.get("market_baseline_home_margin"))
        baseline_win = _safe(g.get("market_home_win_prob"))
        spread_residual = float(spread_residuals[idx])
        win_residual = float(win_residuals[idx])
        final_margin = task_modeling_service.combine_market_and_residual(
            baseline_value=baseline_margin,
            residual_value=spread_residual,
            fallback_baseline=0.0,
        )
        raw_home_prob = task_modeling_service.combine_market_and_residual(
            baseline_value=baseline_win,
            residual_value=win_residual,
            fallback_baseline=0.5,
        )
        raw_home_prob = min(max(float(raw_home_prob), 1e-6), 1 - 1e-6)
        calibrated_home_prob = task_modeling_service.apply_platt_calibration(raw_home_prob, win_calibration)
        market = task_modeling_service.market_baseline(
            market_spread_home=_safe(g.get("market_spread_home")),
            market_home_win_prob=baseline_win,
        )
        out.append({
            "game_id": g["game_id"],
            "home_team_id": g["home_team"],
            "away_team_id": g["away_team"],
            "predicted_spread": round(-float(final_margin), 1),  # negative = home favored
            "predicted_home_margin": round(float(final_margin), 1),
            "predicted_home_win_prob": round(calibrated_home_prob, 3),
            "market_baseline": market,
            "residual_diagnostics": {
                "spread_residual": round(spread_residual, 4),
                "win_residual": round(win_residual, 4),
                "market_used": bool(market["available"]),
            },
            "calibration": {
                "method": (win_calibration or {}).get("method", "identity"),
                "raw_home_win_prob": round(raw_home_prob, 3),
                "calibrated_home_win_prob": round(calibrated_home_prob, 3),
            },
            "task_model_versions": {
                task_modeling_service.TASK_SPREAD_EDGE: TASK_CONFIGS[task_modeling_service.TASK_SPREAD_EDGE].model_version,
                task_modeling_service.TASK_WIN_PROBABILITY: TASK_CONFIGS[task_modeling_service.TASK_WIN_PROBABILITY].model_version,
                task_modeling_service.TASK_PLAYER_PROPS: TASK_CONFIGS[task_modeling_service.TASK_PLAYER_PROPS].model_version,
                task_modeling_service.TASK_FANTASY_PROJECTION: TASK_CONFIGS[task_modeling_service.TASK_FANTASY_PROJECTION].model_version,
            },
        })
    return {"available": True, "games": out}
