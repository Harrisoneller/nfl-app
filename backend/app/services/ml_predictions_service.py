"""Market-blind NFL ML predictions.

Targets are the raw home margin and the raw home win. Closing lines are not
features, labels, priors, or calibration targets. The layer stays off unless a
caller passes include_ml=true.

Contract: research/nfl-ml-blind/PREREG.md
"""
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
from ..utils.teams import canonical_team
from . import feature_store_service, task_modeling_service
from .ml_blind_features import build_market_blind_frame, model_matrix, team_game_epa
from .ml_blind_guard import assert_fit_allowed
from .ml_blind_protocol import inner_oof_plan
from .ml_blind_spec import (
    FEATURE_COLUMNS,
    PLATT_EPOCHS,
    PLATT_LEARNING_RATE,
    SPREAD_MODEL_PARAMS,
    WEIGHT_HALF_LIFE,
    WEIGHT_QB_CHANGE_AWAY,
    WEIGHT_QB_CHANGE_HOME,
    WEIGHT_SHORT_WEEK_AWAY,
    WEIGHT_SHORT_WEEK_HOME,
    WEIGHT_WEATHER_IS_BAD,
    WIN_MODEL_PARAMS,
    assert_no_market_columns,
)

log = get_logger(__name__)
_nfl = NflDataPyAdapter()

_MODEL_DIR = Path(os.environ.get("ML_MODEL_DIR", "/tmp"))
_FEATURES = list(FEATURE_COLUMNS)

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


async def _build_features_for_season(db: Session, season: int) -> pd.DataFrame | None:
    """Per-game features for one season, with the previous season as history.

    ``db`` is unused on purpose. Elo is rebuilt from the schedule cutoff so a
    database snapshot cannot carry a later week into an earlier game.
    """
    del db
    schedules: list[pd.DataFrame] = []
    plays: list[pd.DataFrame] = []
    for year in (season - 1, season):
        sched = await _nfl.schedules_df(year)
        if sched is None or len(sched) == 0:
            continue
        sched = _canonicalize_schedule(sched, year)
        schedules.append(sched)
        pbp = await _nfl.pbp_df(year)
        if pbp is not None and len(pbp):
            plays.append(_canonicalize_pbp(pbp))
    if not schedules:
        return None
    schedule = pd.concat(schedules, ignore_index=True)
    pbp = pd.concat(plays, ignore_index=True) if plays else pd.DataFrame()
    frame = build_market_blind_frame(schedule, team_game_epa(pbp, schedule))
    frame = frame[frame["season"] == season].reset_index(drop=True)
    return frame


def _canonicalize_schedule(sched: pd.DataFrame, season: int) -> pd.DataFrame:
    out = sched.copy()
    out["season"] = season
    for col in ("home_team", "away_team"):
        if col in out.columns:
            out[col] = out[col].map(lambda x: canonical_team(x) if isinstance(x, str) else x)
    return out


def _canonicalize_pbp(pbp: pd.DataFrame) -> pd.DataFrame:
    out = pbp.copy()
    for col in ("posteam", "defteam", "home_team", "away_team"):
        if col in out.columns:
            out[col] = out[col].map(lambda x: canonical_team(x) if isinstance(x, str) else x)
    return out


def _sample_weight(frame: pd.DataFrame) -> pd.Series:
    max_idx = int((frame["season"] * 100 + frame["week_number"]).max())
    recency = frame.apply(
        lambda r: task_modeling_service.exponential_recency_weight(
            current_index=max_idx,
            sample_index=int(r["season"] * 100 + r["week_number"]),
            half_life=WEIGHT_HALF_LIFE,
        ),
        axis=1,
    )
    regime = (
        1.0
        + (WEIGHT_SHORT_WEEK_HOME * frame["short_week_home"].astype(float))
        + (WEIGHT_SHORT_WEEK_AWAY * frame["short_week_away"].astype(float))
        + (WEIGHT_WEATHER_IS_BAD * frame["weather_is_bad"].astype(float))
        + (WEIGHT_QB_CHANGE_HOME * frame["qb_change_proxy_home"].astype(float))
        + (WEIGHT_QB_CHANGE_AWAY * frame["qb_change_proxy_away"].astype(float))
    )
    return recency * regime


def _task_model_file(task: str) -> Path:
    return _MODEL_DIR / f"nfl_{task}_xgb.json"


def _task_calibration_file(task: str) -> Path:
    return _MODEL_DIR / f"nfl_{task}_calibration.json"


def _cached_model_key(task: str) -> str:
    return f"ml_model_loaded:{task}"


def _load_task_calibration(task: str) -> dict[str, Any] | None:
    path = _task_calibration_file(task)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return None


def _save_task_calibration(task: str, payload: dict[str, Any]) -> None:
    path = _task_calibration_file(task)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _training_rows(frame: pd.DataFrame) -> pd.DataFrame:
    assert_no_market_columns(frame.columns)
    data = frame.dropna(subset=["home_margin"]).copy()
    if "game_type" in data.columns:
        data = data[data["game_type"].astype(str).str.upper() == "REG"]
    data["home_win"] = (data["home_margin"] > 0).astype(int)
    return data


def fit_market_blind_models(
    frame: pd.DataFrame,
    locked_sha: str | None = None,
    **guard_kwargs: Any,
) -> dict[str, Any]:
    """Fit the raw-margin regressor and the raw-win classifier. No market columns.

    Platt uses inner-season out-of-fold probabilities only. The guard runs
    before any tree is fit.
    """
    assert_fit_allowed(locked_sha, **guard_kwargs)
    xgb = _xgb()
    if xgb is None:
        return {"trained": False, "reason": "xgboost not installed"}
    data = _training_rows(frame)
    if len(data) == 0:
        return {"trained": False, "reason": "no training data"}
    matrix = model_matrix(data)
    weights = _sample_weight(data)
    spread = xgb.XGBRegressor(**SPREAD_MODEL_PARAMS)
    spread.fit(matrix, data["home_margin"].astype(float), sample_weight=weights)
    win = xgb.XGBClassifier(**WIN_MODEL_PARAMS)
    win.fit(matrix, data["home_win"].astype(int), sample_weight=weights)
    oof_probs, oof_outcomes = _oof_win_probabilities(data, xgb)
    platt = task_modeling_service.fit_platt_calibration(
        raw_probs=oof_probs,
        outcomes=oof_outcomes,
        epochs=PLATT_EPOCHS,
        learning_rate=PLATT_LEARNING_RATE,
    )
    return {
        "trained": True,
        "spread_model": spread,
        "win_model": win,
        "platt": platt,
        "rows": len(data),
        "features": list(matrix.columns),
        "margin_mae": float(abs(spread.predict(matrix) - data["home_margin"].to_numpy()).mean()),
        "oof_probs": oof_probs,
        "oof_outcomes": oof_outcomes,
    }


def _oof_win_probabilities(data: pd.DataFrame, xgb: Any) -> tuple[list[float], list[int]]:
    """Probabilities for inner seasons from models fit only on earlier seasons."""
    probs: list[float] = []
    outcomes: list[int] = []
    for cal_season, prior in inner_oof_plan(data["season"].tolist()):
        if cal_season in prior:
            raise RuntimeError("OOF calibration season leaked into its training fold")
        train = data[data["season"].isin(prior)]
        cal = data[data["season"] == cal_season]
        if len(train) == 0 or len(cal) == 0:
            continue
        model = xgb.XGBClassifier(**WIN_MODEL_PARAMS)
        model.fit(
            model_matrix(train),
            train["home_win"].astype(int),
            sample_weight=_sample_weight(train),
        )
        folded = model.predict_proba(model_matrix(cal))[:, 1]
        probs.extend(float(p) for p in folded)
        outcomes.extend(int(y) for y in cal["home_win"].tolist())
    return probs, outcomes


def predict_market_blind(
    models: dict[str, Any], frame: pd.DataFrame,
) -> tuple[list[float], list[float], list[float]]:
    """Unrounded home margin, calibrated win probability, and raw win probability."""
    assert_no_market_columns(frame.columns)
    matrix = model_matrix(frame)
    margins = [float(v) for v in models["spread_model"].predict(matrix)]
    raw = [float(v) for v in models["win_model"].predict_proba(matrix)[:, 1]]
    calibrated = [
        task_modeling_service.apply_platt_calibration(p, models.get("platt"))
        for p in raw
    ]
    return margins, calibrated, raw


async def train(
    db: Session,
    seasons: list[int],
    locked_sha: str | None = None,
    **guard_kwargs: Any,
) -> dict[str, Any]:
    """Fit the raw-target models and write their artifacts. Does not enable serving."""
    assert_fit_allowed(locked_sha, **guard_kwargs)
    frames: list[pd.DataFrame] = []
    for season in seasons:
        df = await _build_features_for_season(db, season)
        if df is not None and len(df):
            frames.append(df)
    if not frames:
        return {"trained": False, "reason": "no training data"}
    big = pd.concat(frames, ignore_index=True)
    fitted = fit_market_blind_models(big, locked_sha, **guard_kwargs)
    if not fitted.get("trained"):
        return fitted

    results: dict[str, Any] = {}
    for task, cfg in TASK_CONFIGS.items():
        if cfg.is_scaffold or task not in (
            task_modeling_service.TASK_SPREAD_EDGE,
            task_modeling_service.TASK_WIN_PROBABILITY,
        ):
            results[task] = {
                "trained": False,
                "status": "scaffold" if cfg.is_scaffold else "unused",
                "model_version": cfg.model_version,
                "feature_set_version": cfg.feature_set_version,
            }
            continue
        model = fitted["spread_model"] if task == task_modeling_service.TASK_SPREAD_EDGE else fitted["win_model"]
        path = _task_model_file(task)
        path.parent.mkdir(parents=True, exist_ok=True)
        model.save_model(str(path))
        cache.set(_cached_model_key(task), model, 60 * 60 * 6)
        calibration_payload = None
        if task == task_modeling_service.TASK_WIN_PROBABILITY:
            previous = _load_task_calibration(task)
            raw = fitted["oof_probs"]
            outcomes = fitted["oof_outcomes"]
            platt = fitted["platt"]
            calibrated = [
                task_modeling_service.apply_platt_calibration(p, platt) for p in raw
            ]
            metrics = {
                "ece": round(task_modeling_service.expected_calibration_error(calibrated, outcomes), 6),
                "brier": round(task_modeling_service.brier_score(calibrated, outcomes), 6),
                "ece_raw": round(task_modeling_service.expected_calibration_error(raw, outcomes), 6),
                "brier_raw": round(task_modeling_service.brier_score(raw, outcomes), 6),
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
            "rows": fitted["rows"],
            "mae": round(float(fitted["margin_mae"]), 4),
            "model_version": cfg.model_version,
            "feature_set_version": cfg.feature_set_version,
            "calibration": calibration_payload,
            "target_kind": cfg.target_kind,
        }

    primary = results.get(DEFAULT_PRIMARY_TASK, {})
    return {
        "trained": bool(primary.get("trained")),
        "rows": int(fitted["rows"]),
        "in_sample_mae": primary.get("mae"),
        "task_models": results,
    }


def _load_model(task: str):
    xgb = _xgb()
    if xgb is None:
        return None
    cached = cache.get(_cached_model_key(task))
    if cached is not None:
        return cached
    path = _task_model_file(task)
    if not path.exists():
        return None
    model_cls = xgb.XGBClassifier if task == task_modeling_service.TASK_WIN_PROBABILITY else xgb.XGBRegressor
    model = model_cls()
    model.load_model(str(path))
    cache.set(_cached_model_key(task), model, 60 * 60 * 6)
    return model


async def predict_week_ml(db: Session, season: int, week: int) -> dict[str, Any]:
    spread_model = _load_model(task_modeling_service.TASK_SPREAD_EDGE)
    win_model = _load_model(task_modeling_service.TASK_WIN_PROBABILITY)
    if spread_model is None or win_model is None:
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
    platt = _load_task_calibration(task_modeling_service.TASK_WIN_PROBABILITY)
    margins, calibrated, raw = predict_market_blind(
        {"spread_model": spread_model, "win_model": win_model, "platt": platt},
        weekly,
    )
    out = []
    for idx, (_, game) in enumerate(weekly.iterrows()):
        margin = margins[idx]
        out.append({
            "game_id": game["game_id"],
            "home_team_id": game["home_team"],
            "away_team_id": game["away_team"],
            "predicted_spread": round(-margin, 1),
            "predicted_home_margin": round(margin, 1),
            "predicted_home_win_prob": round(calibrated[idx], 3),
            "calibration": {
                "method": (platt or {}).get("method", "identity"),
                "raw_home_win_prob": round(raw[idx], 3),
                "calibrated_home_win_prob": round(calibrated[idx], 3),
            },
            "task_model_versions": {
                task: TASK_CONFIGS[task].model_version for task in TASK_CONFIGS
            },
        })
    return {"available": True, "games": out}
