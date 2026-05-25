"""Model lifecycle orchestration: train -> backtest -> calibrate -> promote."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import ulid
from sqlalchemy.orm import Session

from ..logging_config import get_logger
from ..models.model_lifecycle_run import ModelLifecycleRun
from ..utils.seasons import latest_completed_season
from . import backtest_service, ml_predictions_service, task_modeling_service

log = get_logger(__name__)

LIFECYCLE_MODEL_VERSION = "ml-task-suite-v1"
GATES = {
    "max_brier": 0.24,
    "max_log_loss": 0.69,
    "min_classifier_accuracy_pct": 51.0,
    "min_test_games": 100,
    "min_ats_correct_pct": 50.0,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def evaluate_gates(
    challenger: dict[str, Any],
    champion: dict[str, Any] | None,
) -> dict[str, Any]:
    """Objective gate checks. Lower brier/log_loss, higher accuracy/ATS."""
    checks: dict[str, bool] = {}
    checks["n_games_ok"] = int(challenger.get("n_games", 0)) >= GATES["min_test_games"]
    checks["brier_ok"] = float(challenger.get("brier_score", 1.0)) <= GATES["max_brier"]
    checks["log_loss_ok"] = float(challenger.get("log_loss", 9.0)) <= GATES["max_log_loss"]
    checks["accuracy_ok"] = float(challenger.get("classifier_accuracy_pct", 0.0)) >= GATES["min_classifier_accuracy_pct"]

    ats = challenger.get("ats_correct_pct")
    checks["ats_ok"] = (ats is None) or (float(ats) >= GATES["min_ats_correct_pct"])

    if champion:
        checks["brier_vs_champion_ok"] = float(challenger.get("brier_score", 1.0)) <= float(champion.get("brier_score", 1.0)) + 0.005
        checks["accuracy_vs_champion_ok"] = float(challenger.get("classifier_accuracy_pct", 0.0)) >= float(champion.get("classifier_accuracy_pct", 0.0)) - 0.3
    else:
        checks["brier_vs_champion_ok"] = True
        checks["accuracy_vs_champion_ok"] = True

    approved = all(checks.values())
    return {
        "approved": approved,
        "checks": checks,
        "gates": GATES,
    }


def latest_run(db: Session) -> dict[str, Any] | None:
    row = (
        db.query(ModelLifecycleRun)
        .order_by(ModelLifecycleRun.started_at.desc())
        .first()
    )
    if row is None:
        return None
    return _serialize(row)


def latest_promoted_run(db: Session) -> dict[str, Any] | None:
    row = (
        db.query(ModelLifecycleRun)
        .filter(ModelLifecycleRun.is_promoted.is_(True))
        .order_by(ModelLifecycleRun.started_at.desc())
        .first()
    )
    if row is None:
        return None
    return _serialize(row)


async def run_weekly_lifecycle(
    db: Session,
    *,
    season: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    season = season or latest_completed_season()
    run_key = str(ulid.new())
    started_at = _now()

    latest = latest_run(db)
    if latest and latest.get("season") == season and not force:
        return {
            "status": "skipped_existing",
            "season": season,
            "latest_run": latest,
        }

    champion = latest_promoted_run(db)
    champion_backtest = (champion or {}).get("backtest_payload", {}).get("elo_overall") if champion else None

    run = ModelLifecycleRun(
        run_key=run_key,
        season=season,
        status="running",
        decision="hold",
        is_promoted=False,
        model_version=LIFECYCLE_MODEL_VERSION,
        champion_model_version=(champion or {}).get("model_version"),
        train_payload={},
        backtest_payload={},
        calibration_payload={},
        compare_payload={},
        gate_payload={},
        started_at=started_at,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        train_seasons = list(range(season - 4, season))
        train_payload = await ml_predictions_service.train(db, train_seasons)
        task_models = train_payload.get("task_models", {})
        elo_backtest = await backtest_service.backtest_elo(db)
        ml_backtest = await backtest_service.backtest_ml(db, test_season=season)
        elo_overall = elo_backtest.get("overall", {})
        win_task = task_models.get(task_modeling_service.TASK_WIN_PROBABILITY, {})
        win_calibration = win_task.get("calibration") or {}
        win_metrics = win_calibration.get("metrics") or {}
        win_drift = win_calibration.get("drift") or {}
        calibration = {
            "expected_calibration_error": elo_overall.get("expected_calibration_error"),
            "brier_score": elo_overall.get("brier_score"),
            "log_loss": elo_overall.get("log_loss"),
            "task_calibration": {
                task_modeling_service.TASK_WIN_PROBABILITY: {
                    "method": win_calibration.get("method"),
                    "ece": win_metrics.get("ece"),
                    "brier": win_metrics.get("brier"),
                    "ece_delta": win_drift.get("ece_delta"),
                    "brier_delta": win_drift.get("brier_delta"),
                },
            },
        }

        gate = evaluate_gates(elo_overall, champion_backtest)
        decision = "promote" if gate["approved"] else "hold"
        compare_payload = {
            "champion": champion_backtest,
            "challenger": elo_overall,
            "roi_drift_proxy_ats_delta": None if champion_backtest is None else (
                (elo_overall.get("ats_correct_pct") or 0.0)
                - (champion_backtest.get("ats_correct_pct") or 0.0)
            ),
            "task_model_versions": {
                task: (meta or {}).get("model_version")
                for task, meta in task_models.items()
            },
            "task_calibration_drift": {
                task_modeling_service.TASK_WIN_PROBABILITY: {
                    "ece_delta": win_drift.get("ece_delta"),
                    "brier_delta": win_drift.get("brier_delta"),
                },
            },
        }

        run.train_payload = train_payload
        run.backtest_payload = {
            "elo_overall": elo_overall,
            "ml_oos": ml_backtest,
        }
        run.calibration_payload = calibration
        run.compare_payload = compare_payload
        run.gate_payload = gate
        run.status = "ok"
        run.decision = decision
        run.is_promoted = decision == "promote"
        run.finished_at = _now()
        db.commit()
        db.refresh(run)

        log.info(
            "model_lifecycle_completed",
            season=season,
            decision=decision,
            approved=gate["approved"],
            run_key=run_key,
        )
        return _serialize(run)
    except Exception as e:  # noqa: BLE001
        run.status = "error"
        run.decision = "hold"
        run.finished_at = _now()
        run.compare_payload = {"error": str(e)[:400]}
        db.commit()
        log.warning("model_lifecycle_failed", season=season, error=str(e)[:200], run_key=run_key)
        raise


def _serialize(r: ModelLifecycleRun) -> dict[str, Any]:
    return {
        "id": r.id,
        "run_key": r.run_key,
        "season": r.season,
        "status": r.status,
        "decision": r.decision,
        "is_promoted": r.is_promoted,
        "model_version": r.model_version,
        "champion_model_version": r.champion_model_version,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "train_payload": r.train_payload,
        "backtest_payload": r.backtest_payload,
        "calibration_payload": r.calibration_payload,
        "compare_payload": r.compare_payload,
        "gate_payload": r.gate_payload,
    }
