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
def test_parlay_rejects_out_of_range_and_missing_events(db):
    """1-leg or 9-leg requests fail size validation; missing events fail lookup."""
    # Out of range (under): 1 leg
    with pytest.raises(ValueError):
        sparky_service.rank_parlay(db, ["a"])
    # Out of range (over): 9 legs
    with pytest.raises(ValueError):
        sparky_service.rank_parlay(db, [f"e{i}" for i in range(9)])
    # In-range but every event is missing -> lookup-side ValueError
    with pytest.raises(ValueError):
        sparky_service.rank_parlay(db, ["nope-a", "nope-b"])


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
def test_rank_parlay_variable_n_2_through_5(db):
    """rank_parlay accepts 2..8 legs; persistence + payload reflect n_legs + EV."""
    from datetime import date, datetime, timedelta, timezone

    from app.models.sparky import SparkyGamePrediction, SparkyParlayRanking

    today = date.today()

    # Build 5 minimal SparkyGamePrediction rows the engine can rank.
    teams = [("KC", "DEN"), ("BUF", "NYJ"), ("DAL", "PHI"), ("SF", "SEA"), ("BAL", "PIT")]
    event_ids = [f"varn-{i}" for i in range(len(teams))]
    db.query(SparkyGamePrediction).filter(
        SparkyGamePrediction.event_id.in_(event_ids)
    ).delete(synchronize_session=False)
    db.commit()
    for (home, away), eid in zip(teams, event_ids):
        db.add(SparkyGamePrediction(
            slate_date=today, event_id=eid,
            home_team_id=home, away_team_id=away,
            predicted_winner=home, win_prob=0.62,
            confidence_score=66.0, classification="lean",
            signals=[], explanation="t",
            market={
                "home_ml": -180, "away_ml": 155,
                "favorite": "home", "home_win_prob_ensemble": 0.62,
            },
        ))
    db.commit()

    for n, expected_combos in [(2, 4), (3, 8), (4, 16), (5, 32)]:
        result = sparky_service.rank_parlay(
            db, event_ids[:n], slate_date=today, persist=True,
        )
        assert len(result["parlays"]) == expected_combos
        for p in result["parlays"]:
            assert p["n_legs"] == n
            assert "expected_value" in p
            assert "is_value" in p
            assert "kelly_fraction" in p

        # Persisted rows match the engine output.
        persisted = (
            db.query(SparkyParlayRanking)
            .filter(SparkyParlayRanking.slate_id == "|".join(sorted(event_ids[:n])))
            .all()
        )
        assert len(persisted) == expected_combos
        for r in persisted:
            assert r.n_legs == n
            if n == 2:
                assert r.leg3_event_id is None
            else:
                assert r.leg3_event_id is not None

    # rank_parlay rejects out-of-range and duplicates.
    import pytest as _pt
    with _pt.raises(ValueError):
        sparky_service.rank_parlay(db, event_ids[:1])
    with _pt.raises(ValueError):
        sparky_service.rank_parlay(db, [event_ids[0], event_ids[0]])

    # Cleanup
    db.query(SparkyGamePrediction).filter(
        SparkyGamePrediction.event_id.in_(event_ids)
    ).delete(synchronize_session=False)
    db.query(SparkyParlayRanking).filter(
        SparkyParlayRanking.slate_id.like("varn-%")
        | SparkyParlayRanking.leg1_event_id.like("varn-%")
    ).delete(synchronize_session=False)
    db.commit()
    # `today` and timedelta/datetime/timezone imports above silence any
    # accidental unused-import lints in tooling that scans this file.
    _ = (today, datetime, timedelta, timezone)


@skip_no_db
def test_current_event_rows_constrains_to_one_nfl_week(db):
    """If the Odds API returned Week 1 + Week 2 (+ Week 3), Sparky's slate must
    show only Week 1. Anchor = earliest upcoming kickoff; cap = +6d 12h.
    """
    from datetime import datetime, timedelta, timezone

    from app.models.odds_snapshot import OddsSnapshot

    # Clean any prior week-window fixture rows so the test is idempotent.
    db.query(OddsSnapshot).filter(OddsSnapshot.event_id.like("wk-test-%")).delete(
        synchronize_session=False
    )
    db.commit()

    now = datetime.now(timezone.utc)
    # Week 1: Thursday +3d  /  Sunday +6d  -- both should be in the window
    # Week 2: Thursday +10d  /  Sunday +13d -- both should be OUTSIDE the window
    # Week 3: Sunday +20d                    -- outside
    fixtures = [
        ("wk-test-w1-thu", now + timedelta(days=3)),
        ("wk-test-w1-sun", now + timedelta(days=6)),
        ("wk-test-w2-thu", now + timedelta(days=10)),
        ("wk-test-w2-sun", now + timedelta(days=13)),
        ("wk-test-w3-sun", now + timedelta(days=20)),
    ]
    for event_id, commence in fixtures:
        db.add(OddsSnapshot(
            event_id=event_id, captured_at=now, snapshot_label="T1",
            commence_time=commence,
            home_team="Test Home", away_team="Test Away",
            home_team_id="KC", away_team_id="DEN",
            book="TestBook", home_ml=-150, away_ml=130,
            home_implied=0.6, away_implied=0.4, favorite="home",
            raw={"test": True},
        ))
    db.commit()

    rows = sparky_service._current_event_rows(db)
    keys = {k for k in rows.keys() if k.startswith("wk-test-")}
    assert keys == {"wk-test-w1-thu", "wk-test-w1-sun"}, (
        f"Expected only Week 1 events, got: {keys}"
    )

    # Cleanup
    db.query(OddsSnapshot).filter(OddsSnapshot.event_id.like("wk-test-%")).delete(
        synchronize_session=False
    )
    db.commit()


@skip_no_db
def test_build_slate_is_idempotent_wipes_stale_predictions(db):
    """A second build must REPLACE the persisted slate, not add to it.

    Without the wipe-on-rebuild, predictions from an earlier build that
    included more games (older code, wider week window) would linger on the
    same slate_date — exactly the bug that left multi-week games on the
    dashboard even after the week-window filter shipped.
    """
    from datetime import date, datetime, timedelta, timezone

    from app.models.odds_snapshot import OddsSnapshot
    from app.models.sparky import SparkyGamePrediction, SparkyParlayRanking

    today = date.today()

    # Clean any prior fixture rows so the test is idempotent itself.
    db.query(SparkyGamePrediction).filter(
        SparkyGamePrediction.event_id.like("idem-test-%")
    ).delete(synchronize_session=False)
    db.query(SparkyGamePrediction).filter(
        SparkyGamePrediction.slate_date == today
    ).delete(synchronize_session=False)
    db.query(SparkyParlayRanking).filter(
        SparkyParlayRanking.slate_date == today
    ).delete(synchronize_session=False)
    db.query(OddsSnapshot).filter(OddsSnapshot.event_id.like("idem-test-%")).delete(
        synchronize_session=False
    )
    db.commit()

    # 1) Insert a "stale" prediction directly (simulating an earlier build that
    #    included games no longer in the week window).
    db.add(SparkyGamePrediction(
        slate_date=today,
        event_id="idem-test-stale",
        home_team_id="DAL",
        away_team_id="PHI",
        predicted_winner="PHI",
        win_prob=0.6,
        confidence_score=60.0,
        classification="lean",
        signals=[],
        explanation="stale",
        market={},
    ))

    # 2) Seed a single upcoming odds snapshot so build_slate has exactly one
    #    real event to write.
    now = datetime.now(timezone.utc)
    db.add(OddsSnapshot(
        event_id="idem-test-current",
        captured_at=now,
        snapshot_label="T1",
        commence_time=now + timedelta(days=3),
        home_team="Kansas City Chiefs", away_team="Denver Broncos",
        home_team_id="KC", away_team_id="DEN",
        book="TestBook", home_ml=-180, away_ml=155,
        home_implied=0.64, away_implied=0.36, favorite="home",
        raw={"test": True},
    ))
    db.commit()

    # 3) Rebuild.
    asyncio.run(sparky_service.build_slate(db))

    # 4) Stale row gone; only the freshly-built one remains.
    survivors = (
        db.query(SparkyGamePrediction)
        .filter(SparkyGamePrediction.slate_date == today)
        .all()
    )
    event_ids = {p.event_id for p in survivors}
    assert "idem-test-stale" not in event_ids, "Stale prediction was not wiped on rebuild"
    assert "idem-test-current" in event_ids, "New prediction was not written"

    # Cleanup
    db.query(SparkyGamePrediction).filter(
        SparkyGamePrediction.slate_date == today
    ).delete(synchronize_session=False)
    db.query(SparkyParlayRanking).filter(
        SparkyParlayRanking.slate_date == today
    ).delete(synchronize_session=False)
    db.query(OddsSnapshot).filter(OddsSnapshot.event_id.like("idem-test-%")).delete(
        synchronize_session=False
    )
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
