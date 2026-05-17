"""Backtesting + model evaluation.

Two predictions to evaluate:
  1. **Elo** — for every completed game in the last N seasons, look up the
     pre-game Elo for both teams (Week N-1 of that season) and compute
     predicted spread / win prob. Compare to actuals. Aggregate MAE,
     classification accuracy, calibration.
  2. **XGBoost ML model** — train on 2020-2023 seasons, evaluate on 2024 as
     an out-of-sample test. The in-sample MAE shipped with the model file is
     not a real performance measure.

Outputs are aggressively cached because both are static once computed.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from ..adapters.data.nfl_data_py_adapter import NflDataPyAdapter
from ..cache import cache
from ..logging_config import get_logger
from ..models.elo import TeamEloRating
from ..utils.seasons import latest_completed_season
from ..utils.teams import canonical_team
from . import elo_service, ml_predictions_service

log = get_logger(__name__)
_nfl = NflDataPyAdapter()

CACHE_TTL = 60 * 60 * 24  # 24h


# ============================================================================
# Elo backtest
# ============================================================================


async def backtest_elo(db: Session, seasons: list[int] | None = None) -> dict[str, Any]:
    """Walk completed games using week-N-1 Elo as the pre-game prediction.

    For each game we compute:
      - predicted spread (from Elo diff + HFA)
      - predicted win prob
      - actual margin + winner
    Then aggregate MAE/RMSE/accuracy and calibration.
    """
    if seasons is None:
        latest = latest_completed_season()
        seasons = list(range(latest - 4, latest + 1))
    key = f"backtest:elo:{','.join(str(s) for s in seasons)}"
    if (v := cache.get(key)) is not None:
        return v

    # Load all Elo ratings for the seasons we're testing.
    elo_rows = (
        db.query(TeamEloRating)
        .filter(TeamEloRating.season.in_(seasons))
        .all()
    )
    elo_by_key: dict[tuple[str, int, int], float] = {
        (r.team_id, r.season, r.week): r.rating for r in elo_rows
    }

    per_season: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []

    for season in seasons:
        sched = await _nfl.schedules_df(season)
        if sched is None or len(sched) == 0:
            continue
        sched = sched.copy()
        sched["home_team"] = sched["home_team"].map(
            lambda x: canonical_team(x) if isinstance(x, str) else x
        )
        sched["away_team"] = sched["away_team"].map(
            lambda x: canonical_team(x) if isinstance(x, str) else x
        )
        played = sched.dropna(subset=["home_score", "away_score"]).copy()
        if len(played) == 0:
            continue

        season_rows: list[dict[str, Any]] = []
        for _, g in played.iterrows():
            h, a = g["home_team"], g["away_team"]
            week = int(g["week"]) if pd.notna(g.get("week")) else None
            if not h or not a or week is None:
                continue
            # Pre-game Elo = previous week's rating (or Week 0 baseline)
            prev_week = max(week - 1, 0)
            home_elo = elo_by_key.get((h, season, prev_week))
            away_elo = elo_by_key.get((a, season, prev_week))
            if home_elo is None or away_elo is None:
                continue

            neutral = bool(g.get("location") == "Neutral") if "location" in played.columns else False
            win_prob_home = elo_service.win_probability(home_elo, away_elo, neutral_site=neutral)
            pred_spread = elo_service.predicted_spread(home_elo, away_elo, neutral_site=neutral)

            actual_margin = int(g["home_score"]) - int(g["away_score"])
            home_won = actual_margin > 0

            row = {
                "season": season,
                "week": week,
                "home": h, "away": a,
                "home_elo": home_elo, "away_elo": away_elo,
                "pred_win_prob_home": win_prob_home,
                "pred_spread": pred_spread,        # negative = home favored
                "actual_margin": actual_margin,
                "home_won": home_won,
                # Closing-line ATS for comparison if available
                "closing_spread": float(g["spread_line"]) if "spread_line" in g and pd.notna(g.get("spread_line")) else None,
            }
            season_rows.append(row)
            all_rows.append(row)

        if season_rows:
            per_season.append({
                "season": season,
                "n_games": len(season_rows),
                **_aggregate(season_rows),
            })

    overall = _aggregate(all_rows) if all_rows else {}
    calibration = _calibration_table(all_rows)

    out = {
        "seasons": seasons,
        "n_games": len(all_rows),
        "overall": overall,
        "per_season": per_season,
        "calibration": calibration,
    }
    cache.set(key, out, CACHE_TTL)
    return out


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute MAE/RMSE/classification metrics for a list of game rows."""
    n = len(rows)
    if n == 0:
        return {}
    spread_errs = []
    classifier_correct = 0
    prob_brier = 0.0
    high_conf_correct = 0
    high_conf_n = 0
    ats_correct = 0
    ats_n = 0
    for r in rows:
        # Spread MAE: predicted home margin = -pred_spread
        predicted_home_margin = -r["pred_spread"]
        err = abs(predicted_home_margin - r["actual_margin"])
        spread_errs.append(err)

        # Win-prob classifier: did the model pick the actual winner?
        pred_home = r["pred_win_prob_home"] >= 0.5
        if pred_home == r["home_won"]:
            classifier_correct += 1

        # Brier score: (predicted_prob - actual_outcome)^2
        actual_home_int = 1 if r["home_won"] else 0
        prob_brier += (r["pred_win_prob_home"] - actual_home_int) ** 2

        # High-confidence (>=60%) accuracy
        if r["pred_win_prob_home"] >= 0.6 or r["pred_win_prob_home"] <= 0.4:
            high_conf_n += 1
            if pred_home == r["home_won"]:
                high_conf_correct += 1

        # ATS: did we beat the closing line?
        if r["closing_spread"] is not None:
            ats_n += 1
            # Our model's pick relative to the close
            model_picks_home = r["pred_spread"] < r["closing_spread"]
            # Did the home team cover the close?
            home_covered = r["actual_margin"] > r["closing_spread"]
            if model_picks_home == home_covered:
                ats_correct += 1

    mae = sum(spread_errs) / n
    rmse = (sum(e * e for e in spread_errs) / n) ** 0.5
    return {
        "spread_mae": round(mae, 2),
        "spread_rmse": round(rmse, 2),
        "classifier_accuracy_pct": round(100 * classifier_correct / n, 1),
        "brier_score": round(prob_brier / n, 4),
        "high_confidence_accuracy_pct": (
            round(100 * high_conf_correct / high_conf_n, 1) if high_conf_n else None
        ),
        "high_confidence_n": high_conf_n,
        "ats_picks_n": ats_n,
        "ats_correct_pct": round(100 * ats_correct / ats_n, 1) if ats_n else None,
    }


def _calibration_table(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bin predictions by win-prob decile and compute actual win rate per bin.

    A perfectly calibrated model has actual_win_rate ≈ predicted_win_prob for
    every bin. The UI plots this as a diagonal-or-off-diagonal line.
    """
    if not rows:
        return []
    buckets = [
        (0.0, 0.1), (0.1, 0.2), (0.2, 0.3), (0.3, 0.4), (0.4, 0.5),
        (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01),
    ]
    out = []
    for lo, hi in buckets:
        bucket_rows = [r for r in rows if lo <= r["pred_win_prob_home"] < hi]
        n = len(bucket_rows)
        if n == 0:
            out.append({"bin_lo": lo, "bin_hi": hi, "n": 0,
                        "predicted_avg": None, "actual_win_rate": None})
            continue
        pred_avg = sum(r["pred_win_prob_home"] for r in bucket_rows) / n
        actual_rate = sum(1 for r in bucket_rows if r["home_won"]) / n
        out.append({
            "bin_lo": round(lo, 2), "bin_hi": round(hi, 2),
            "n": n,
            "predicted_avg": round(pred_avg, 3),
            "actual_win_rate": round(actual_rate, 3),
        })
    return out


# ============================================================================
# ML out-of-sample evaluation
# ============================================================================


async def backtest_ml(
    db: Session, test_season: int | None = None, train_seasons: list[int] | None = None,
) -> dict[str, Any]:
    """Train XGBoost on `train_seasons`, evaluate on `test_season` (out-of-sample).

    Default: train on the four seasons preceding the test season, test on the
    most recent completed season. Returns MAE/RMSE/accuracy for the held-out
    season + a feature-importance summary.
    """
    latest = latest_completed_season()
    test_season = test_season or latest
    train_seasons = train_seasons or list(range(test_season - 4, test_season))

    key = f"backtest:ml:{test_season}:{','.join(str(s) for s in train_seasons)}"
    if (v := cache.get(key)) is not None:
        return v

    xgb = ml_predictions_service._xgb()  # noqa: SLF001
    if xgb is None:
        return {"available": False, "reason": "xgboost not installed"}

    # Build training frame
    train_frames = []
    for s in train_seasons:
        df = await ml_predictions_service._build_features_for_season(db, s)  # noqa: SLF001
        if df is not None:
            train_frames.append(df.dropna(subset=["home_margin"]))
    if not train_frames:
        return {"available": False, "reason": "no training data"}
    train = pd.concat(train_frames, ignore_index=True)

    test_df = await ml_predictions_service._build_features_for_season(db, test_season)  # noqa: SLF001
    if test_df is None:
        return {"available": False, "reason": "no test data"}
    test = test_df.dropna(subset=["home_margin"])
    if len(test) == 0:
        return {"available": False, "reason": "test set is empty"}

    feats = ml_predictions_service._FEATURES  # noqa: SLF001
    X_train, y_train = train[feats], train["home_margin"]
    X_test, y_test = test[feats], test["home_margin"]

    model = xgb.XGBRegressor(
        n_estimators=300, max_depth=4, learning_rate=0.05,
        subsample=0.85, colsample_bytree=0.85, random_state=42,
    )
    model.fit(X_train, y_train)
    preds = model.predict(X_test)

    errs = np.abs(preds - y_test.values)
    mae = float(errs.mean())
    rmse = float(np.sqrt((errs ** 2).mean()))
    # Classification: did the model pick the actual winner?
    correct = float(((preds > 0) == (y_test.values > 0)).mean()) * 100

    # Feature importance
    importance = sorted(
        zip(feats, model.feature_importances_.tolist()),
        key=lambda x: -x[1],
    )
    feat_imp = [{"feature": f, "importance": round(imp, 4)} for f, imp in importance]

    out = {
        "available": True,
        "train_seasons": train_seasons,
        "test_season": test_season,
        "n_train": int(len(train)),
        "n_test": int(len(test)),
        "spread_mae": round(mae, 2),
        "spread_rmse": round(rmse, 2),
        "classifier_accuracy_pct": round(correct, 1),
        "feature_importance": feat_imp,
    }
    cache.set(key, out, CACHE_TTL)
    return out


async def backtest_summary(db: Session) -> dict[str, Any]:
    """Both backtests in one call for the Performance page."""
    elo = await backtest_elo(db)
    ml = await backtest_ml(db)
    return {"elo": elo, "ml": ml}
