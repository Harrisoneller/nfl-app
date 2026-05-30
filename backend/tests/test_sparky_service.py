"""DB-backed integration tests for the Sparky service.

These exercise the full orchestration (snapshot history -> slate build ->
parlay ranking -> accuracy) against a real database. They auto-skip if the
configured database isn't reachable (e.g. CI without Postgres), so they're safe
to keep in the suite and run for real on a dev machine:

    cd backend && source .venv/bin/activate
    alembic upgrade head
    pytest tests/test_sparky_service.py -v

The model JSON columns are Postgres JSONB, so these target the app's normal
Postgres database. They clean up the synthetic ``demo-*`` rows they create.
"""
from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.integration

try:  # graceful skip if the app's DB stack / connection isn't available
    from sqlalchemy import text

    from app.db import SessionLocal
    from app.services import sparky_service

    _db = SessionLocal()
    _db.execute(text("SELECT 1"))
    _db.close()
    _DB_OK = True
except Exception as _e:  # noqa: BLE001
    _DB_OK = False
    _SKIP_REASON = f"database unavailable: {str(_e)[:80]}"

skip_no_db = pytest.mark.skipif(not _DB_OK, reason="database not reachable" if _DB_OK else "")


@pytest.fixture()
def db():
    if not _DB_OK:
        pytest.skip(_SKIP_REASON)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@skip_no_db
def test_backfill_then_full_pipeline(db):
    # 1) Seed synthetic history + a live-looking current slate.
    result = sparky_service.backfill_demo(db, days=12, seed=42)
    assert result["picks_settled"] > 0
    assert result["parlays_settled"] > 0
    assert result["current_slate_games"] == 6

    # 2) Build the current slate from the seeded snapshots.
    slate = asyncio.run(sparky_service.build_slate(db))
    assert slate["count"] >= 3
    for g in slate["games"]:
        assert g["predicted_winner"] in (g["home_team_id"], g["away_team_id"])
        assert 0 <= g["confidence_score"] <= 100
        assert 0.0 <= g["win_prob"] <= 1.0

    # 3) get_slate returns the persisted slate with recommended parlays.
    got = sparky_service.get_slate(db)
    assert got["count"] == slate["count"]
    assert len(got["recommended_parlays"]) == 8  # all 8 combos of the top 3 games

    # 4) Rank a parlay for three chosen events -> exactly 8, ranks 1..8.
    event_ids = [g["event_id"] for g in got["games"][:3]]
    parlay = sparky_service.rank_parlay(db, event_ids)
    assert len(parlay["parlays"]) == 8
    assert [p["rank"] for p in parlay["parlays"]] == list(range(1, 9))
    comps = [p["composite_score"] for p in parlay["parlays"]]
    assert comps == sorted(comps, reverse=True)

    # 5) Accuracy reporting has populated rolling windows + parlay rates.
    acc = sparky_service.historical_accuracy(db)
    assert acc["individual_picks"]["overall"]["n"] > 0
    assert acc["parlays"]["overall"]["n"] > 0
    assert acc["individual_picks"]["rolling"]["rolling_30d"]["n"] > 0

    # 6) Admin status reflects a ready pipeline.
    status = sparky_service.admin_status(db)
    assert status["pipeline_ready"] is True
    assert status["snapshot_events"] >= 6

    # Cleanup synthetic rows so reruns stay clean.
    _cleanup(db)


@skip_no_db
def test_parlay_requires_three_events(db):
    with pytest.raises(ValueError):
        sparky_service.rank_parlay(db, ["a", "b"])


def _cleanup(db):
    from app.models.odds_snapshot import OddsSnapshot
    from app.models.sparky import (
        SparkyGamePrediction,
        SparkyHistoricalResult,
        SparkyParlayRanking,
        SparkyParlayResult,
    )

    db.query(OddsSnapshot).filter(OddsSnapshot.event_id.like("demo-%")).delete(
        synchronize_session=False
    )
    db.query(SparkyGamePrediction).filter(
        SparkyGamePrediction.event_id.like("demo-%")
    ).delete(synchronize_session=False)
    db.query(SparkyParlayRanking).delete(synchronize_session=False)
    db.query(SparkyHistoricalResult).delete(synchronize_session=False)
    db.query(SparkyParlayResult).delete(synchronize_session=False)
    db.commit()


@skip_no_db
def test_settle_sparky_results_writes_historical_from_final_games(db):
    """Settlement should turn a final Game + Sparky prediction into a HistoricalResult row."""
    from datetime import date, datetime, timezone

    from app.models.game import Game
    from app.models.sparky import SparkyGamePrediction, SparkyHistoricalResult

    today = date.today()
    event_id = f"settle-test-{int(datetime.now().timestamp())}"

    # Clean any prior
    db.query(SparkyHistoricalResult).filter(SparkyHistoricalResult.event_id == event_id).delete()
    db.query(SparkyGamePrediction).filter(SparkyGamePrediction.event_id == event_id).delete()
    db.query(Game).filter(Game.id == event_id).delete()
    db.commit()

    # Create a minimal final game
    g = Game(
        id=event_id,
        season=2025,
        week=5,
        status="final",
        status_detail="Final",
        home_team_id="KC",
        away_team_id="BUF",
        home_score=27,
        away_score=20,
        start_time=datetime.now(timezone.utc),
    )
    db.add(g)

    # Create a Sparky prediction that picked the winner
    p = SparkyGamePrediction(
        slate_date=today,
        event_id=event_id,
        home_team_id="KC",
        away_team_id="BUF",
        predicted_winner="KC",
        win_prob=0.68,
        confidence_score=78.0,
        classification="strong_lean",
        signals=[],
        explanation="test",
        market={},
    )
    db.add(p)
    db.commit()

    # Run settlement
    res = sparky_service.settle_sparky_results(db, lookback_days=1)
    assert res["settled_picks"] >= 1

    # Verify the historical row
    hr = (
        db.query(SparkyHistoricalResult)
        .filter(SparkyHistoricalResult.event_id == event_id)
        .first()
    )
    assert hr is not None
    assert hr.predicted_winner == "KC"
    assert hr.actual_winner == "KC"
    assert hr.prediction_correct is True

    # Cleanup
    db.query(SparkyHistoricalResult).filter(SparkyHistoricalResult.event_id == event_id).delete()
    db.query(SparkyGamePrediction).filter(SparkyGamePrediction.event_id == event_id).delete()
    db.query(Game).filter(Game.id == event_id).delete()
    db.commit()
