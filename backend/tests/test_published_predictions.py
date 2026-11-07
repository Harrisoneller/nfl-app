"""One stored qb-ridge-v5 headline per game, shared by every surface.

The headline math is unchanged: market.w_cap stays 0.92 and the stored number
is whatever the current blend step produced. A row from that step is not
served once the headline pipeline id changes.
"""
from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.models.game import Game
from app.models.published_prediction import PredictionRefreshLease, PublishedGamePrediction
from app.services import (
    artifact_cache,
    betting_service,
    market_service,
    predictions_service,
    sparky_service,
)
from app.services.predictions_service import PREDICTION_MODEL_VERSION, _assemble_week_from_store
from app.services.published_predictions import (
    current_headline_pipeline,
    h2h_cache_key,
    h2h_cache_prefix,
    input_revision,
    publication_cutoff_sql,
    publication_generation,
    publish_game,
    refresh_future_games,
    row_is_current,
    select_upcoming_meeting,
    try_acquire_lease,
)
from app.utils.seasons import current_or_upcoming_season


def _engine():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False, "timeout": 15},
    )
    Game.__table__.create(bind=engine)
    PublishedGamePrediction.__table__.create(bind=engine)
    PredictionRefreshLease.__table__.create(bind=engine)
    return engine


def _session(engine):
    return sessionmaker(bind=engine, future=True)()


def _game(db, game_id, home, away, start, *, status="scheduled", week=10, season=2026):
    db.add(Game(
        id=game_id,
        season=season,
        week=week,
        season_type=2,
        start_time=start,
        status=status,
        home_team_id=home,
        away_team_id=away,
        raw={},
    ))
    db.commit()
    return db.get(Game, game_id)


def _v5(game_id, spread, *, basis="market-blend-v1"):
    return {
        "id": game_id,
        "home_team_id": "DAL",
        "away_team_id": "IND",
        "prediction": {
            "model_version": PREDICTION_MODEL_VERSION,
            "predicted_spread": spread,
            "predicted_total": 47.5,
            "home_win_prob": 0.62,
            "away_win_prob": 0.38,
            "prediction_basis": basis,
            "inputs": {"v5": {"margin": 4.8, "model_version": PREDICTION_MODEL_VERSION}},
            "distribution": {"expected_margin": -spread, "margin_sd": 13.5},
        },
    }


def test_headline_pipeline_is_the_current_blend_and_weight_is_unchanged():
    from app.services.param_registry import value

    assert market_service.BLEND_VERSION == "market-blend-v1"
    assert market_service.HEADLINE_PIPELINE == "market-blend-v1"
    assert current_headline_pipeline() == "market-blend-v1"
    assert value("market.w_cap") == 0.92


def test_postgres_cutoff_uses_wall_clock_not_frozen_now():
    sql = publication_cutoff_sql("postgresql")
    assert "clock_timestamp()" in sql
    assert "now()" not in sql
    assert "julianday" in publication_cutoff_sql("sqlite")


def test_publish_future_game_bumps_generation_and_records_pipeline():
    engine = _engine()
    db = _session(engine)
    start = datetime.now(UTC) + timedelta(days=3)
    game = _game(db, "2026_10_IND_DAL", "DAL", "IND", start)
    status = publish_game(db, game, _v5(game.id, -4.8), "rev-1")
    assert status == "published"
    row = db.get(PublishedGamePrediction, game.id)
    assert row.headline_pipeline == "market-blend-v1"
    assert row.prediction_basis == "market-blend-v1"
    assert row.model_version == PREDICTION_MODEL_VERSION
    assert publication_generation(db) == 1
    assert row_is_current(row)
    db.close()


def test_past_kickoff_and_started_status_do_not_replace_the_row():
    engine = _engine()
    db = _session(engine)
    future = datetime.now(UTC) + timedelta(days=2)
    game = _game(db, "g", "DAL", "IND", future)
    assert publish_game(db, game, _v5("g", -4.8), "rev") == "published"
    db.execute(text(
        "UPDATE games SET start_time = '2020-01-01 00:00:00', status = 'scheduled' WHERE id = 'g'"
    ))
    db.commit()
    game = db.get(Game, "g")
    assert publish_game(db, game, _v5("g", -0.5), "rev-2") == "rejected_after_kickoff"
    row = db.get(PublishedGamePrediction, "g")
    db.refresh(row)
    body = row.payload if isinstance(row.payload, dict) else __import__("json").loads(row.payload)
    assert body["prediction"]["predicted_spread"] == -4.8
    assert row.frozen

    db.execute(text(
        "UPDATE games SET start_time = '2035-01-01 18:00:00', status = 'in' WHERE id = 'g2'"
    ))
    _game(db, "g2", "SEA", "ARI", datetime(2035, 1, 1, 18, tzinfo=UTC), status="in")
    assert publish_game(db, db.get(Game, "g2"), _v5("g2", -3.0), "rev") == "rejected_after_kickoff"
    assert db.get(PublishedGamePrediction, "g2") is None

    _game(db, "g3", "SEA", "ARI", datetime(2035, 6, 1, tzinfo=UTC), status="final")
    assert publish_game(db, db.get(Game, "g3"), _v5("g3", -3.0), "rev") == "rejected_after_kickoff"
    db.close()


def test_missing_kickoff_can_publish_and_is_not_frozen():
    engine = _engine()
    db = _session(engine)
    game = _game(db, "unknown", "DAL", "IND", None)
    assert publish_game(db, game, _v5("unknown", -4.8), "rev") == "published_kickoff_unknown"
    row = db.get(PublishedGamePrediction, "unknown")
    assert row.kickoff_unknown
    assert not row.frozen
    db.close()


def test_waiter_does_not_publish_after_kickoff_lands_under_the_lock(tmp_path):
    # A memory database is per connection. Two threads need one file.
    engine = create_engine(
        f"sqlite:///{tmp_path / 'pub.db'}",
        future=True,
        connect_args={"check_same_thread": False, "timeout": 15},
    )
    Game.__table__.create(bind=engine)
    PublishedGamePrediction.__table__.create(bind=engine)
    PredictionRefreshLease.__table__.create(bind=engine)
    db = _session(engine)
    start = datetime.now(UTC) + timedelta(days=2)
    game = _game(db, "g", "DAL", "IND", start)
    assert publish_game(db, game, _v5("g", -4.8), "rev") == "published"
    db.close()

    holder = _session(engine)
    holder.execute(text("BEGIN IMMEDIATE"))
    holder.execute(text("UPDATE prediction_refresh_leases SET reason = 'hold' WHERE id = 1"))
    outcome: dict[str, str] = {}

    def _wait() -> None:
        session = _session(engine)
        try:
            fresh = session.get(Game, "g")
            outcome["status"] = publish_game(session, fresh, _v5("g", -0.5), "later")
        finally:
            session.close()

    thread = threading.Thread(target=_wait)
    thread.start()
    # Let the waiter block on the publication lock before kickoff moves.
    thread.join(0.4)
    holder.execute(text(
        "UPDATE games SET start_time = '2020-01-01 00:00:00', status = 'in' WHERE id = 'g'"
    ))
    holder.commit()
    thread.join(timeout=15)
    assert not thread.is_alive()
    assert outcome["status"] == "rejected_after_kickoff"
    check = _session(engine)
    row = check.get(PublishedGamePrediction, "g")
    body = row.payload if isinstance(row.payload, dict) else __import__("json").loads(row.payload)
    assert body["prediction"]["predicted_spread"] == -4.8
    check.close()
    holder.close()


def test_stale_blend_pipeline_is_not_served_as_current_v5(monkeypatch):
    engine = _engine()
    db = _session(engine)
    season = current_or_upcoming_season()
    start = datetime.now(UTC) + timedelta(days=2)
    game = _game(db, "2026_10_IND_DAL", "DAL", "IND", start, season=season)
    assert publish_game(db, game, _v5(game.id, -4.8), input_revision(db, season=season)) == "published"
    db.execute(text(
        "UPDATE games SET start_time = '2020-01-01 00:00:00' WHERE id = :id"
    ), {"id": game.id})
    db.commit()
    monkeypatch.setattr(market_service, "HEADLINE_PIPELINE", "headline-model")
    assert not row_is_current(db.get(PublishedGamePrediction, game.id))

    async def _no_live(*_args, **_kwargs):
        raise AssertionError("live fallback after kickoff")

    monkeypatch.setattr(predictions_service, "_predict_week_uncached", _no_live)
    payload = asyncio.run(_assemble_week_from_store(
        db, season, 10, input_revision(db, season=season),
    ))
    served = payload["games"][0]
    assert served["prediction"] is None
    assert served["unavailable_reason"] == "stale_pipeline"
    assert served["model_version"] == PREDICTION_MODEL_VERSION
    stored = db.get(PublishedGamePrediction, game.id)
    body = stored.payload if isinstance(stored.payload, dict) else __import__("json").loads(stored.payload)
    assert body["prediction"]["predicted_spread"] == -4.8
    db.close()


def test_pipeline_change_changes_input_revision(monkeypatch):
    engine = _engine()
    db = _session(engine)
    before = input_revision(db, season=2026)
    monkeypatch.setattr(market_service, "HEADLINE_PIPELINE", "headline-model")
    after = input_revision(db, season=2026)
    assert before != after
    db.close()


def test_h2h_cache_key_includes_model_revision_and_generation():
    prefix = h2h_cache_prefix("SEA", "ARI", 2026, PREDICTION_MODEL_VERSION)
    first = h2h_cache_key("SEA", "ARI", 2026, 4, PREDICTION_MODEL_VERSION, "rev-a", 1)
    bumped = h2h_cache_key("SEA", "ARI", 2026, 4, PREDICTION_MODEL_VERSION, "rev-a", 2)
    revised = h2h_cache_key("SEA", "ARI", 2026, 4, PREDICTION_MODEL_VERSION, "rev-b", 1)
    assert first != bumped != revised
    assert first.startswith(prefix)
    assert "qb-ridge-v5" in first
    legacy = "SEA:ARI:2026:v4"
    assert not legacy.startswith(prefix)


def test_upcoming_meeting_skips_the_played_rematch():
    now = datetime(2026, 11, 7, tzinfo=UTC)
    chosen = select_upcoming_meeting([
        {
            "game_id": "2026_02_ARI_SEA",
            "week": 2,
            "played": True,
            "kickoff": datetime(2026, 9, 14, tzinfo=UTC),
        },
        {
            "game_id": "2026_14_SEA_ARI",
            "week": 14,
            "played": False,
            "kickoff": datetime(2026, 12, 7, tzinfo=UTC),
        },
    ], now)
    assert chosen["game_id"] == "2026_14_SEA_ARI"
    earlier = select_upcoming_meeting([
        {
            "game_id": "early",
            "week": 2,
            "played": False,
            "kickoff": datetime(2026, 11, 10, tzinfo=UTC),
        },
        {
            "game_id": "later",
            "week": 14,
            "played": False,
            "kickoff": datetime(2026, 12, 7, tzinfo=UTC),
        },
    ], now)
    assert earlier["game_id"] == "early"


def test_surfaces_share_the_stored_headline(monkeypatch):
    engine = _engine()
    db = _session(engine)
    season = current_or_upcoming_season()
    start = datetime.now(UTC) + timedelta(days=4)
    game = _game(db, "2026_10_IND_DAL", "DAL", "IND", start, season=season, week=10)
    revision = input_revision(db, season=season)
    assert publish_game(db, game, _v5(game.id, -4.8), revision) == "published"

    async def _passthrough(**kwargs):
        return await kwargs["compute"]()

    async def _no_live(*_args, **_kwargs):
        raise AssertionError("request path recomputed a stored game")

    async def _no_market(_db=None):
        return {}

    monkeypatch.setattr(artifact_cache, "get_or_compute", _passthrough)
    monkeypatch.setattr(predictions_service, "_predict_week_uncached", _no_live)
    monkeypatch.setattr(betting_service, "_current_market_odds", _no_market)

    async def _read():
        week = await predictions_service.predict_week(db, season, 10)
        remaining = await predictions_service.team_remaining_schedule_predictions(db, "DAL", season)
        edge = await betting_service.games_with_edge(db, season, 10)
        sparky = await sparky_service._model_pred_map(db)
        return week, remaining, edge, sparky

    week, remaining, edge, sparky = asyncio.run(_read())
    assert week["games"][0]["prediction"]["predicted_spread"] == -4.8
    assert week["games"][0]["model_version"] == PREDICTION_MODEL_VERSION
    assert week["games"][0]["prediction_source"] == "stored"
    assert week["games"][0]["headline_pipeline"] == "market-blend-v1"
    dal = next(g for g in remaining["games"] if g["id"] == game.id)
    assert dal["predicted_spread_for_team"] == -4.8
    assert dal["model_version"] == PREDICTION_MODEL_VERSION
    assert edge["games"][0]["prediction"]["predicted_spread"] == -4.8
    assert sparky[("DAL", "IND")]["pred_margin"] == 4.8
    assert sparky[("DAL", "IND")]["prediction_source"] == "stored"
    assert sparky[("DAL", "IND")]["model_version"] == PREDICTION_MODEL_VERSION
    db.close()


def test_sparky_keeps_fallback_provenance(monkeypatch):
    async def _fake_week(_db, _season, _week):
        return {"games": [{
            "home_team_id": "DAL",
            "away_team_id": "IND",
            "prediction_source": "v5_fallback",
            "prediction": {
                "home_win_prob": 0.61,
                "predicted_spread": -4.8,
                "predicted_total": 44.0,
                "model_version": PREDICTION_MODEL_VERSION,
                "prediction_source": "v5_fallback",
                "distribution": {"expected_margin": 4.8, "margin_sd": 13.0},
            },
        }]}

    monkeypatch.setattr(predictions_service, "predict_week", _fake_week)

    async def _read():
        return await sparky_service._model_pred_map(None)

    mapped = asyncio.run(_read())
    row = mapped[("DAL", "IND")]
    assert row["prediction_source"] == "v5_fallback"
    assert row["model_version"] == PREDICTION_MODEL_VERSION
    assert row["pred_margin"] == 4.8


def test_refresh_coalesces_and_does_not_blank_a_row():
    engine = _engine()
    db = _session(engine)
    start = datetime.now(UTC) + timedelta(days=3)
    _game(db, "g", "DAL", "IND", start)

    async def _compute(_db, _season, _week):
        return {"season": _season, "week": _week, "games": [_v5("g", -4.8)]}

    first = asyncio.run(refresh_future_games(
        db, compute_week=_compute, reason="process_start", max_weeks=2, max_games=4,
    ))
    assert first["status"] == "ok"
    assert first["published"] == 1

    async def _empty(_db, _season, _week):
        return {"games": []}

    kept = asyncio.run(refresh_future_games(
        db, compute_week=_empty, reason="process_start", max_weeks=2, max_games=4,
    ))
    assert kept["published"] == 0
    row = db.get(PublishedGamePrediction, "g")
    body = row.payload if isinstance(row.payload, dict) else __import__("json").loads(row.payload)
    assert body["prediction"]["predicted_spread"] == -4.8

    assert try_acquire_lease(db, datetime.now(UTC), "busy")
    calls = {"n": 0}

    async def _count(_db, _season, _week):
        calls["n"] += 1
        return {"games": []}

    coalesced = asyncio.run(refresh_future_games(
        db, compute_week=_count, reason="scheduler_interval",
    ))
    assert coalesced["status"] == "coalesced"
    assert calls["n"] == 0
    db.close()
