"""Sparky API — NFL betting prediction & parlay intelligence (mounted at /sparky).

Read endpoints serve persisted model output (cheap, no API spend). Admin
endpoints (refresh / backfill) recompute or seed data; they're grouped under
/sparky/admin and intended for the in-app admin/debug view.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..deps import get_db
from ..schemas.sparky import (
    AccuracyOut,
    AdminStatusOut,
    GameDetailOut,
    ParlayOut,
    ParlayRequest,
    SignalGlossaryOut,
    SlateOut,
)
from ..services import sparky_service
from ..services.sparky import backtest as sparky_backtest
from ..services.sparky.signals import SIGNAL_DEFINITIONS

router = APIRouter()


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(400, "date must be ISO format YYYY-MM-DD") from None


@router.get("/slate", response_model=SlateOut)
def slate(date: str | None = None, prefer_real: bool = False, db: Session = Depends(get_db)):
    """Today's (or a given date's) slate: prediction cards + recommended parlays.

    Set prefer_real=true to exclude synthetic demo data (event_ids starting with 'demo-')
    and prefer the real current/upcoming schedule.
    """
    return sparky_service.get_slate(db, _parse_date(date), prefer_real=prefer_real)


@router.get("/games/{event_id}", response_model=GameDetailOut)
def game_detail(event_id: str, db: Session = Depends(get_db)):
    """Full detail for one game: prediction, signals, line movement, books."""
    detail = sparky_service.game_detail(db, event_id)
    if detail.get("prediction") is None and not detail.get("books"):
        raise HTTPException(404, f"No Sparky data for event {event_id}")
    return detail


@router.post("/parlay", response_model=ParlayOut)
def parlay(body: ParlayRequest, db: Session = Depends(get_db)):
    """Rank all 8 combinations for three selected games."""
    try:
        return sparky_service.rank_parlay(db, body.event_ids, persist=body.persist)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.get("/accuracy", response_model=AccuracyOut)
def accuracy(as_of: str | None = None, db: Session = Depends(get_db)):
    """Historical accuracy: rolling windows, by-band, by-signal, parlay rates."""
    return sparky_service.historical_accuracy(db, as_of=_parse_date(as_of))


@router.get("/signals/glossary", response_model=SignalGlossaryOut)
def signal_glossary():
    """The signal taxonomy + definitions (for the UI glossary / admin view)."""
    return {
        "signals": [
            {"key": key, **meta} for key, meta in SIGNAL_DEFINITIONS.items()
        ]
    }


# --- Admin / debug ---------------------------------------------------------- #


@router.get("/admin/status", response_model=AdminStatusOut)
def admin_status(db: Session = Depends(get_db)):
    """Pipeline health: snapshot counts, last pull, prediction/result counts."""
    return sparky_service.admin_status(db)


@router.post("/admin/refresh", response_model=SlateOut)
async def admin_refresh(date: str | None = None, db: Session = Depends(get_db)):
    """Force-rebuild the slate from the current odds snapshots."""
    return await sparky_service.build_slate(db, slate_date=_parse_date(date))


@router.post("/admin/build_real", response_model=SlateOut)
async def admin_build_real(db: Session = Depends(get_db)):
    """
    Clear any synthetic demo Sparky data and build fresh predictions
    from the real current/upcoming schedule + live odds + model.
    This is the best way to view the actual Week 1 slate in Sparky.
    """
    # Remove demo data so it doesn't pollute the most-recent slate
    from ..models.sparky import SparkyGamePrediction, SparkyParlayRanking

    db.query(SparkyGamePrediction).filter(
        SparkyGamePrediction.event_id.like("demo-%")
    ).delete(synchronize_session=False)

    db.query(SparkyParlayRanking).filter(
        SparkyParlayRanking.slate_id.like("demo-%") | 
        SparkyParlayRanking.leg1_event_id.like("demo-%")
    ).delete(synchronize_session=False)

    db.commit()

    # Build real predictions for whatever is currently in snapshots/lines
    return await sparky_service.build_slate(db)


@router.post("/admin/backfill")
async def admin_backfill(days: int = 30, db: Session = Depends(get_db)):
    """Seed deterministic synthetic history + a live-looking current slate.

    Use this to populate the dashboard, movement charts, and accuracy views in
    the offseason (or before any live odds have been captured).
    """
    days = max(1, min(120, days))
    result = sparky_service.backfill_demo(db, days=days)
    # Immediately build the seeded current slate so /slate returns data.
    slate = await sparky_service.build_slate(db)
    return {**result, "slate_built": slate.get("count", 0)}


@router.post("/admin/settle")
def admin_settle(days: int = 14, db: Session = Depends(get_db)):
    """Run outcome settlement for recent slates whose games have final scores.

    This is the production mechanism that feeds the Historical Accuracy view
    with real results (as opposed to the demo backfill which seeds synthetic
    settled rows). Safe to call repeatedly; settlement is idempotent.
    """
    days = max(1, min(60, days))
    result = sparky_service.settle_sparky_results(db, lookback_days=days)
    return {
        "ok": True,
        "lookback_days": result["lookback_days"],
        "settled_picks": result["settled_picks"],
        "settled_parlays": result["settled_parlays"],
        "skipped": result["skipped"],
    }


@router.post("/admin/backtest")
def admin_backtest(
    start: str,
    end: str,
    mode: str = "replay",
    hours_cutoff: float | None = None,
    db: Session = Depends(get_db),
):
    """
    Run a historical Sparky backtest.

    This is the primary validation tool. Use it to measure whether the current
    engine (signals + confidence + parlays) would have performed well on past
    market data.

    Returns a rich metrics payload (accuracy, Brier, calibration, signal lift,
    simulated ROI, etc.).
    """
    try:
        start_d = date.fromisoformat(start)
        end_d = date.fromisoformat(end)
    except ValueError:
        raise HTTPException(400, "start and end must be YYYY-MM-DD")

    if mode not in ("replay", "settled"):
        raise HTTPException(400, "mode must be 'replay' or 'settled'")

    cfg = sparky_backtest.BacktestConfig(
        start_date=start_d,
        end_date=end_d,
        mode=mode,
        hours_before_kickoff_cutoff=hours_cutoff,
    )
    result = sparky_backtest.run_backtest(db, cfg)

    # Defensive: ensure no NaN values leak into the JSON response
    def safe_num(v):
        if v is None:
            return None
        try:
            if isinstance(v, float) and (v != v or v == float("inf") or v == float("-inf")):
                return None
            return v
        except Exception:
            return None

    return {
        "config": {
            "start": str(cfg.start_date),
            "end": str(cfg.end_date),
            "mode": cfg.mode,
            "hours_cutoff": cfg.hours_before_kickoff_cutoff,
        },
        "summary": result.summary(),
        "metrics": {
            "pick_accuracy": result.pick_accuracy,
            "brier_score": safe_num(result.brier_score),
            "log_loss": safe_num(result.log_loss),
            "calibration": result.calibration,
            "signal_performance": result.signal_performance[:10],
            "parlay": result.parlay_metrics,
            "roi": result.roi_simulation,
        },
        "n_games": result.n_games,
        "n_slates": result.n_slates,
        "generated_at": result.generated_at.isoformat(),
    }
