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
from . import artifact_cache, elo_service, ml_predictions_service, prediction_dist

log = get_logger(__name__)
_nfl = NflDataPyAdapter()

CACHE_TTL = 60 * 60 * 24  # 24h


# ============================================================================
# Elo backtest
# ============================================================================


async def backtest_elo(db: Session, seasons: list[int] | None = None) -> dict[str, Any]:
    """Walk completed games using week-N-1 Elo as the pre-game prediction.

    Persisted to model_artifacts. Completed seasons are immutable, so the
    artifact survives indefinitely once written — the only refresh trigger
    is a new completed season ending.
    """
    if seasons is None:
        latest = latest_completed_season()
        seasons = list(range(latest - 4, latest + 1))

    artifact_key = f"seasons:{','.join(str(s) for s in seasons)}"

    async def _compute() -> dict[str, Any]:
        return await _backtest_elo_compute(db, seasons)

    return await artifact_cache.get_or_compute(
        kind="backtest_elo",
        key=artifact_key,
        compute=_compute,
        ttl_seconds=60 * 60 * 24 * 7,  # 1 week — refreshed when new data lands
        l1_ttl_seconds=60 * 60,
    )


async def _backtest_elo_compute(
    db: Session, seasons: list[int],
) -> dict[str, Any]:
    """The actual backtest computation, wrapped above for caching."""

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
    if all_rows:
        overall["expected_calibration_error"] = _expected_calibration_error(calibration)

    return {
        "seasons": seasons,
        "n_games": len(all_rows),
        "overall": overall,
        "per_season": per_season,
        "calibration": calibration,
        # Does our margin distribution have the right SPREAD, not just the right
        # center? CRPS grades the full distribution; the PIT histogram should be
        # flat if NFL_MARGIN_SIGMA is calibrated (U-shape = too narrow).
        "distribution": _distribution_eval(all_rows),
        # The only external standard that matters: how do we compare to the
        # closing line (and to naive baselines) on the same games?
        "baselines": _baseline_metrics(all_rows),
    }


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute MAE/RMSE/classification metrics for a list of game rows."""
    n = len(rows)
    if n == 0:
        return {}
    spread_errs = []
    classifier_correct = 0
    prob_brier = 0.0
    prob_log_loss = 0.0
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

        # Brier + log loss: proper scoring rules for the win probability.
        actual_home_int = 1 if r["home_won"] else 0
        prob_brier += (r["pred_win_prob_home"] - actual_home_int) ** 2
        prob_log_loss += prediction_dist.log_loss(r["pred_win_prob_home"], actual_home_int)

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
        "log_loss": round(prob_log_loss / n, 4),
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


def _expected_calibration_error(calibration: list[dict[str, Any]]) -> float | None:
    """Weighted |predicted − actual| across the reliability table.

    Same definition as ``task_modeling_service.expected_calibration_error``,
    applied to the decile table ``_calibration_table`` already built. Empty
    bins are skipped. ``None`` when no graded games landed in a bin — callers
    treat that as "unknown" (``calibration_score_from_ece``), not as perfect.
    """
    total = 0
    acc = 0.0
    for bucket in calibration:
        n = int(bucket.get("n") or 0)
        predicted = bucket.get("predicted_avg")
        actual = bucket.get("actual_win_rate")
        if n <= 0 or predicted is None or actual is None:
            continue
        total += n
        acc += abs(float(predicted) - float(actual)) * n
    if total == 0:
        return None
    return round(acc / total, 4)


# PIT shape is only called once a sample is large enough that a 12-point
# share gap is not one weird week. Tails = outer four deciles, center =
# 0.3–0.7; both groups are 40% of a uniform histogram.
_PIT_BINS = 10
_PIT_MIN_GAMES = 30
_PIT_SHAPE_GAP = 0.12


def _margin_sigma() -> float:
    """Sigma the board actually ships (registry, else NFL_MARGIN_SIGMA)."""
    sigma = float(prediction_dist.margin_sigma())
    if sigma <= 0:
        return float(prediction_dist.NFL_MARGIN_SIGMA)
    return sigma


def _pit_shape(counts: list[int]) -> str:
    """'u_shape' = too narrow, 'domed' = too wide, 'flat' = calibrated."""
    n = sum(counts)
    if n < _PIT_MIN_GAMES:
        return "insufficient"
    tail = counts[0] + counts[1] + counts[-2] + counts[-1]
    center = sum(counts[3:7])
    tail_share = tail / n
    center_share = center / n
    if tail_share >= center_share + _PIT_SHAPE_GAP and tail_share >= 0.52:
        return "u_shape"
    if center_share >= tail_share + _PIT_SHAPE_GAP and center_share >= 0.52:
        return "domed"
    return "flat"


def _distribution_eval(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """CRPS and a PIT histogram for the margin distribution.

    Predicted home margin is ``-pred_spread`` (sportsbook spread, negative =
    home favored). PIT is the normal CDF of the realized margin. A flat
    histogram means ``margin_sigma`` is calibrated; a U-shape means it is too
    narrow; a dome means it is too wide.
    """
    sigma = _margin_sigma()
    histogram = [
        {
            "bin_lo": round(i / _PIT_BINS, 1),
            "bin_hi": round((i + 1) / _PIT_BINS, 1),
            "n": 0,
            "share": None,
        }
        for i in range(_PIT_BINS)
    ]
    if not rows:
        return {
            "n": 0,
            "margin_sigma": round(sigma, 2),
            "crps": None,
            "pit_mean": None,
            "pit_shape": "insufficient",
            "pit_histogram": histogram,
        }

    crps_sum = 0.0
    pit_sum = 0.0
    counts = [0] * _PIT_BINS
    for row in rows:
        expected = -float(row["pred_spread"])
        actual = float(row["actual_margin"])
        crps_sum += prediction_dist.crps_normal(expected, sigma, actual)
        if sigma <= 0:
            pit = 1.0 if actual > expected else (0.0 if actual < expected else 0.5)
        else:
            pit = prediction_dist.norm_cdf((actual - expected) / sigma)
        pit = min(max(pit, 0.0), 1.0)
        pit_sum += pit
        # pit == 1 lands in the last decile; int(1.0 * 10) would be out of range.
        counts[min(_PIT_BINS - 1, int(pit * _PIT_BINS))] += 1

    n = len(rows)
    for i, count in enumerate(counts):
        histogram[i]["n"] = count
        histogram[i]["share"] = round(count / n, 3)
    return {
        "n": n,
        "margin_sigma": round(sigma, 2),
        "crps": round(crps_sum / n, 3),
        "pit_mean": round(pit_sum / n, 3),
        "pit_shape": _pit_shape(counts),
        "pit_histogram": histogram,
    }


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _empty_score() -> dict[str, Any]:
    return {
        "n_games": 0,
        "classifier_accuracy_pct": None,
        "brier_score": None,
        "log_loss": None,
        "spread_mae": None,
    }


def _score_forecasts(
    forecasts: list[tuple[float, float, float, bool]],
) -> dict[str, Any]:
    """Score (home_win_prob, predicted_home_margin, actual_margin, home_won)."""
    n = len(forecasts)
    if n == 0:
        return _empty_score()
    correct = 0
    brier = 0.0
    log_loss = 0.0
    mae = 0.0
    for prob, predicted_margin, actual_margin, home_won in forecasts:
        outcome = 1 if home_won else 0
        if (prob >= 0.5) == bool(home_won):
            correct += 1
        brier += (prob - outcome) ** 2
        log_loss += prediction_dist.log_loss(prob, outcome)
        mae += abs(predicted_margin - actual_margin)
    return {
        "n_games": n,
        "classifier_accuracy_pct": round(100 * correct / n, 1),
        "brier_score": round(brier / n, 4),
        "log_loss": round(log_loss / n, 4),
        "spread_mae": round(mae / n, 2),
    }


def _baseline_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Elo vs a home-field-only forecast and vs the closing line.

    ``closing_spread`` uses the same convention as ``pred_spread``: negative
    means the home team is favored. The market's expected margin is therefore
    ``-closing_spread``. Games missing a line are left out of the market
    block, and ``elo_on_same_games`` rescores Elo on that subset so the
    comparison is not mixed with unpriced games.
    """
    if not rows:
        market = _empty_score()
        market["elo_on_same_games"] = _empty_score()
        return {"n_games": 0, "home": _empty_score(), "elo": _empty_score(), "market": market}

    home_prob = elo_service.win_probability(
        elo_service.INITIAL_RATING, elo_service.INITIAL_RATING,
    )
    home_margin = -elo_service.predicted_spread(
        elo_service.INITIAL_RATING, elo_service.INITIAL_RATING,
    )
    sigma = _margin_sigma()

    home_rows: list[tuple[float, float, float, bool]] = []
    elo_rows: list[tuple[float, float, float, bool]] = []
    market_rows: list[tuple[float, float, float, bool]] = []
    elo_on_market: list[tuple[float, float, float, bool]] = []

    for row in rows:
        actual = float(row["actual_margin"])
        home_won = bool(row["home_won"])
        elo_margin = -float(row["pred_spread"])
        elo_prob = float(row["pred_win_prob_home"])
        home_rows.append((home_prob, home_margin, actual, home_won))
        elo_rows.append((elo_prob, elo_margin, actual, home_won))
        closing = _finite(row.get("closing_spread"))
        if closing is None:
            continue
        market_margin = -closing
        market_rows.append((
            prediction_dist.win_prob(market_margin, sigma),
            market_margin,
            actual,
            home_won,
        ))
        elo_on_market.append((elo_prob, elo_margin, actual, home_won))

    market = _score_forecasts(market_rows)
    market["elo_on_same_games"] = _score_forecasts(elo_on_market)
    return {
        "n_games": len(rows),
        "home": _score_forecasts(home_rows),
        "elo": _score_forecasts(elo_rows),
        "market": market,
    }


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

    xgb = ml_predictions_service._xgb()  # noqa: SLF001
    if xgb is None:
        return {"available": False, "reason": "xgboost not installed"}

    artifact_key = f"test:{test_season}:train:{','.join(str(s) for s in train_seasons)}"

    async def _compute() -> dict[str, Any]:
        return await _backtest_ml_compute(db, test_season, train_seasons, xgb)

    return await artifact_cache.get_or_compute(
        kind="backtest_ml",
        key=artifact_key,
        compute=_compute,
        ttl_seconds=60 * 60 * 24 * 7,
        l1_ttl_seconds=60 * 60,
    )


async def _backtest_ml_compute(
    db: Session, test_season: int, train_seasons: list[int], xgb,
) -> dict[str, Any]:

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

    return {
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


async def backtest_summary(db: Session) -> dict[str, Any]:
    """Both backtests in one call for the Performance page."""
    elo = await backtest_elo(db)
    ml = await backtest_ml(db)
    return {"elo": elo, "ml": ml}
