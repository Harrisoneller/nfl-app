"""Sparky historical results upsert on event_id and do not retry a stored event.

Production logged ``duplicate key value violates unique constraint
"uq_sparky_result_event"`` every scores tick for event
259b5df9aa0d10257f56de78523495e0. Two predictions for that event were inserted
in one transaction; the unique violation rolled the batch back, so the job
tried the same INSERT forever.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker

from app.models.bet import Bet, BetLeg
from app.models.game import Game
from app.models.sparky import (
    SparkyGamePrediction,
    SparkyHistoricalResult,
    SparkyParlayRanking,
    SparkyParlayResult,
)
from app.models.user import User
from app.services import sparky_service
from app.services.sparky_result_store import (
    build_historical_result_upsert,
    historical_result_values,
    upsert_historical_result,
)

EVENT_ID = "259b5df9aa0d10257f56de78523495e0"


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", future=True)
    for table in (
        User.__table__,
        Game.__table__,
        Bet.__table__,
        BetLeg.__table__,
        SparkyGamePrediction.__table__,
        SparkyHistoricalResult.__table__,
        SparkyParlayRanking.__table__,
        SparkyParlayResult.__table__,
    ):
        table.create(bind=engine)
    Session = sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False, future=True
    )
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _values(**overrides):
    base = historical_result_values(
        event_id=EVENT_ID,
        slate_date=date(2025, 9, 7),
        game_id=EVENT_ID,
        predicted_winner="KC",
        actual_winner="KC",
        confidence_score=78.0,
        classification="strong_lean",
        signal_keys=["closing_steam"],
        win_prob=0.68,
        settled_at=datetime(2025, 9, 8, tzinfo=timezone.utc),
    )
    base.update(overrides)
    if "prediction_correct" not in overrides:
        base["prediction_correct"] = base["predicted_winner"] == base["actual_winner"]
    return base


def test_upsert_sql_conflicts_on_event_id_and_updates_only_when_result_changes():
    stmt = build_historical_result_upsert(_values(), dialect="postgresql")
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    compact = " ".join(sql.split())
    assert "ON CONFLICT (event_id) DO UPDATE" in compact
    assert "IS DISTINCT FROM" in compact
    assert "predicted_winner" in compact
    assert "actual_winner" in compact
    assert "prediction_correct" in compact


def test_conflict_does_nothing_until_the_result_changes(db):
    first = _values()
    changed = _values(
        actual_winner="BUF",
        settled_at=datetime(2025, 9, 9, tzinfo=timezone.utc),
    )
    same_again = _values(
        settled_at=datetime(2025, 9, 10, tzinfo=timezone.utc),
    )

    assert db.execute(build_historical_result_upsert(first, dialect="sqlite")).rowcount == 1
    skipped = db.execute(build_historical_result_upsert(same_again, dialect="sqlite"))
    assert skipped.rowcount == 0
    db.commit()

    row = db.query(SparkyHistoricalResult).one()
    assert row.actual_winner == "KC"
    assert row.prediction_correct is True
    stored_at = row.settled_at
    if stored_at is not None and stored_at.tzinfo is None:
        stored_at = stored_at.replace(tzinfo=timezone.utc)
    assert stored_at == first["settled_at"]

    updated = db.execute(build_historical_result_upsert(changed, dialect="sqlite"))
    assert updated.rowcount == 1
    db.commit()
    db.expire_all()
    row = db.query(SparkyHistoricalResult).one()
    assert row.actual_winner == "BUF"
    assert row.prediction_correct is False
    assert db.query(SparkyHistoricalResult).count() == 1


def _prediction(slate: date, winner: str = "KC") -> SparkyGamePrediction:
    return SparkyGamePrediction(
        slate_date=slate,
        event_id=EVENT_ID,
        home_team_id="KC",
        away_team_id="BUF",
        predicted_winner=winner,
        win_prob=0.68,
        confidence_score=78.0,
        classification="strong_lean",
        signals=[{"key": "closing_steam"}],
        explanation="test",
        market={},
        commence_time=datetime(2025, 9, 7, 17, tzinfo=timezone.utc),
    )


def _final_game(db) -> None:
    db.add(Game(
        id=EVENT_ID,
        season=2025,
        week=1,
        season_type=2,
        status="final",
        status_detail="Final",
        home_team_id="KC",
        away_team_id="BUF",
        home_score=27,
        away_score=20,
        start_time=datetime(2025, 9, 7, 17, tzinfo=timezone.utc),
        raw={},
    ))


def _inserts(engine) -> list[str]:
    found: list[str] = []

    def recv(conn, cursor, statement, parameters, context, executemany):
        sql = " ".join(statement.lower().split())
        if "sparky_historical_results" in sql and sql.startswith("insert"):
            found.append(sql)

    event.listen(engine, "before_cursor_execute", recv)
    return found


class _FixedDate(date):
    """Stand-in for ``datetime.date`` so lookback stays on the fixture week."""

    @classmethod
    def today(cls) -> date:
        return date(2025, 9, 8)


def test_duplicate_predictions_settle_once_and_are_not_retried(db, monkeypatch):
    """The production event collided with itself on every 30s scores run."""
    monkeypatch.setattr(sparky_service, "date", _FixedDate)
    _final_game(db)
    db.add(_prediction(date(2025, 9, 7)))
    db.add(_prediction(date(2025, 9, 8)))
    db.commit()

    first = sparky_service.settle_sparky_results(db, lookback_days=14)
    assert first["settled_picks"] == 1
    assert first["games_rolled_back"] == 0
    rows = db.query(SparkyHistoricalResult).all()
    assert len(rows) == 1
    assert rows[0].event_id == EVENT_ID
    assert rows[0].actual_winner == "KC"
    assert rows[0].prediction_correct is True
    assert db.get(Game, EVENT_ID).settlement_status == "settled"

    inserts = _inserts(db.get_bind())
    second = sparky_service.settle_sparky_results(db, lookback_days=14)
    assert second["settled_picks"] == 0
    assert second["already_stored"] == 1
    assert inserts == []
    assert db.query(SparkyHistoricalResult).count() == 1


def test_service_updates_a_changed_result_once(db, monkeypatch):
    monkeypatch.setattr(sparky_service, "date", _FixedDate)
    _final_game(db)
    db.add(_prediction(date(2025, 9, 8), winner="BUF"))
    db.commit()

    # Stored row disagrees with the final score. Settlement should correct it
    # once, then leave it alone.
    db.execute(build_historical_result_upsert(
        _values(predicted_winner="BUF", actual_winner="BUF"),
        dialect="sqlite",
    ))
    db.commit()

    corrected = sparky_service.settle_sparky_results(db, lookback_days=14)
    assert corrected["settled_picks"] == 1
    db.expire_all()
    row = db.query(SparkyHistoricalResult).one()
    assert row.predicted_winner == "BUF"
    assert row.actual_winner == "KC"
    assert row.prediction_correct is False

    inserts = _inserts(db.get_bind())
    again = sparky_service.settle_sparky_results(db, lookback_days=14)
    assert again["settled_picks"] == 0
    assert again["already_stored"] == 1
    assert inserts == []


def test_upsert_helper_skips_the_statement_when_the_row_matches(db):
    engine = db.get_bind()
    inserts = _inserts(engine)
    assert upsert_historical_result(db, _values()) == "written"
    db.commit()
    inserts.clear()
    assert upsert_historical_result(db, _values(
        settled_at=datetime(2025, 10, 1, tzinfo=timezone.utc),
        confidence_score=10.0,
    )) == "unchanged"
    assert inserts == []
    # Confidence is not part of the graded result, so it stays put.
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(SparkyHistoricalResult)) == 1
    assert db.query(SparkyHistoricalResult).one().confidence_score == 78.0
