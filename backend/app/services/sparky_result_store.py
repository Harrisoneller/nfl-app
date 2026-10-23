"""Idempotent writes for ``sparky_historical_results``.

The scores job used to SELECT-then-INSERT a row per prediction. Two predictions
for one ``event_id`` (or two workers) both missed the pending row — autoflush
is off — and Postgres raised

    duplicate key value violates unique constraint "uq_sparky_result_event"

on every 30s tick. The failed INSERT never committed, so the job retried the
same event forever. Each failed statement was large enough to bloat WAL.

The write is now a single ``INSERT ... ON CONFLICT (event_id) DO UPDATE``
whose ``WHERE`` clause is true only when the graded result changed. When it is
false, Postgres updates zero rows (``DO NOTHING``). Callers also skip the
statement entirely once a stored row already matches, so a settled event is
not sent to the database again.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models.sparky import SparkyHistoricalResult

# Graded outcome. Confidence and signal keys are receipts, not the result:
# a change in those alone must not rewrite the row.
_RESULT_FIELDS = ("predicted_winner", "actual_winner", "prediction_correct")


def result_unchanged(row: SparkyHistoricalResult, values: dict[str, Any]) -> bool:
    return all(getattr(row, field) == values[field] for field in _RESULT_FIELDS)


def historical_result_values(
    *,
    event_id: str,
    slate_date,
    game_id: str | None,
    predicted_winner: str | None,
    actual_winner: str | None,
    confidence_score: float | None,
    classification: str | None,
    signal_keys: list,
    win_prob: float | None = None,
    model_prob: float | None = None,
    market_prob: float | None = None,
    settled_at: datetime | None = None,
) -> dict[str, Any]:
    now = settled_at or datetime.now(timezone.utc)
    return {
        "event_id": event_id,
        "game_id": game_id,
        "slate_date": slate_date,
        "sport": "NFL",
        "predicted_winner": predicted_winner,
        "actual_winner": actual_winner,
        "prediction_correct": predicted_winner == actual_winner,
        "confidence_score": confidence_score,
        "classification": classification,
        "signal_keys": list(signal_keys or []),
        "settled_at": now,
        "win_prob": win_prob,
        "model_prob": model_prob,
        "market_prob": market_prob,
        "created_at": now,
        "updated_at": now,
    }


def build_historical_result_upsert(values: dict[str, Any], *, dialect: str):
    """``INSERT`` on ``event_id``; update only when the graded result changed."""
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert

    stmt = insert(SparkyHistoricalResult).values(**values)
    excluded = stmt.excluded
    changed = or_(
        SparkyHistoricalResult.predicted_winner.is_distinct_from(excluded.predicted_winner),
        SparkyHistoricalResult.actual_winner.is_distinct_from(excluded.actual_winner),
        SparkyHistoricalResult.prediction_correct.is_distinct_from(
            excluded.prediction_correct
        ),
    )
    return stmt.on_conflict_do_update(
        index_elements=["event_id"],
        set_={
            "game_id": excluded.game_id,
            "slate_date": excluded.slate_date,
            "sport": excluded.sport,
            "predicted_winner": excluded.predicted_winner,
            "actual_winner": excluded.actual_winner,
            "prediction_correct": excluded.prediction_correct,
            "confidence_score": excluded.confidence_score,
            "classification": excluded.classification,
            "signal_keys": excluded.signal_keys,
            "settled_at": excluded.settled_at,
            "win_prob": excluded.win_prob,
            "model_prob": excluded.model_prob,
            "market_prob": excluded.market_prob,
            "updated_at": excluded.updated_at,
        },
        where=changed,
    )


def upsert_historical_result(db: Session, values: dict[str, Any]) -> str:
    """Insert or update one result.

    Returns ``"written"`` when a row was inserted or its result changed, and
    ``"unchanged"`` when the stored result already matches. The unchanged path
    does not execute an INSERT.
    """
    existing = (
        db.query(SparkyHistoricalResult)
        .filter(SparkyHistoricalResult.event_id == values["event_id"])
        .first()
    )
    if existing is not None and result_unchanged(existing, values):
        return "unchanged"

    dialect = db.get_bind().dialect.name
    result = db.execute(build_historical_result_upsert(values, dialect=dialect))
    if existing is not None:
        # Core UPDATE does not refresh the identity map. Drop it so a later
        # lookup in this session does not treat the old result as current and
        # issue the statement again.
        db.expire(existing)
    if (result.rowcount or 0) == 0:
        return "unchanged"
    return "written"
