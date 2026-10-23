"""Scores and bet settlement commit per game and do not double-write."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.orm import sessionmaker

from app.models.bet import Bet, BetLeg
from app.models.game import Game
from app.models.odds_snapshot import OddsSnapshot
from app.models.team import Team
from app.models.user import User
from app.services import bet_service, scores_service
from scripts.reset_stuck_settlements import (
    format_report,
    guard_local_database,
    parse_args,
    run,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", future=True)
    tables = [
        User.__table__,
        Team.__table__,
        Game.__table__,
        Bet.__table__,
        BetLeg.__table__,
        OddsSnapshot.__table__,
    ]
    for table in tables:
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


def _user(db) -> User:
    user = User(email="bettor@example.com", display_name="Bettor", hashed_password="x")
    db.add(user)
    db.commit()
    return user


def _game(db, game_id: str, home: str, away: str, hs: int, as_: int) -> Game:
    game = Game(
        id=game_id,
        season=2025,
        week=1,
        season_type=2,
        start_time=datetime.now(timezone.utc) - timedelta(hours=4),
        status="final",
        home_team_id=home,
        away_team_id=away,
        home_score=hs,
        away_score=as_,
        raw={},
    )
    db.add(game)
    db.commit()
    return game


def _straight(db, user: User, event_id: str, selection: str, home: str, away: str) -> Bet:
    return bet_service.create_bet(
        db,
        user.id,
        type("Payload", (), {
            "bet_type": "straight",
            "source": "manual",
            "note": "",
            "stake_units": 2.0,
            "stake_dollars": None,
            "placed_at": datetime.now(timezone.utc),
            "legs": [
                type("Leg", (), {
                    "event_id": event_id,
                    "game_id": event_id,
                    "market": "moneyline",
                    "selection": selection,
                    "selection_label": f"{selection} ML",
                    "line": None,
                    "player_name": None,
                    "prop_market": None,
                    "odds_american": 150,
                    "home_team_id": home,
                    "away_team_id": away,
                    "commence_time": None,
                })(),
            ],
        })(),
    )


def test_rerunning_settlement_does_not_double_grade(db):
    user = _user(db)
    _game(db, "g1", "PHI", "DAL", 27, 17)
    bet = _straight(db, user, "g1", "PHI", "PHI", "DAL")

    first = bet_service.settle_user_bets(db, user.id)
    assert first["settled_bets"] == 1
    assert first["graded_legs"] == 1
    db.refresh(bet)
    assert bet.status == "won"
    assert bet.result_units == pytest.approx(3.0)
    settled_at = bet.settled_at
    leg_settled_at = bet.legs[0].settled_at

    second = bet_service.settle_user_bets(db, user.id)
    assert second["settled_bets"] == 0
    assert second["graded_legs"] == 0
    db.refresh(bet)
    assert bet.result_units == pytest.approx(3.0)
    assert bet.settled_at == settled_at
    assert bet.legs[0].leg_result == "won"
    assert bet.legs[0].settled_at == leg_settled_at
    assert db.query(Bet).count() == 1
    assert db.query(BetLeg).count() == 1


def test_failed_game_rolls_back_and_does_not_retry_until_reset(db, monkeypatch):
    user = _user(db)
    _game(db, "g1", "PHI", "DAL", 27, 17)
    _game(db, "g2", "KC", "DEN", 30, 10)
    good = _straight(db, user, "g1", "PHI", "PHI", "DAL")
    bad = _straight(db, user, "g2", "KC", "KC", "DEN")

    real = bet_service._grade_leg

    def boom(session, leg):
        game = bet_service._find_game(session, leg)
        if game is not None and game.id == "g2":
            leg.leg_result = "won"
            raise RuntimeError("grade failed")
        return real(session, leg)

    monkeypatch.setattr(bet_service, "_grade_leg", boom)
    result = bet_service.settle_user_bets(db, user.id)
    assert result["settled_bets"] == 1
    assert result["games_rolled_back"] == 1

    db.expire_all()
    good = db.get(Bet, good.id)
    bad = db.get(Bet, bad.id)
    assert good.status == "won"
    assert good.result_units == pytest.approx(3.0)
    assert bad.status == "pending"
    assert bad.result_units is None
    assert bad.legs[0].leg_result == "pending"
    assert db.get(Game, "g1").settlement_status == "settled"
    assert db.get(Game, "g2").settlement_status == "settling"

    monkeypatch.setattr(bet_service, "_grade_leg", real)
    again = bet_service.settle_user_bets(db, user.id)
    assert again["settled_bets"] == 0
    assert again["games_stuck"] == 1
    db.expire_all()
    assert db.get(Bet, bad.id).status == "pending"

    preview = run(db, apply=False)
    assert preview["dry_run"] is True
    assert preview["stuck_game_ids"] == ["g2"]
    assert preview["reset"] == 0
    assert "no rows changed" in format_report(preview)
    db.expire_all()
    assert db.get(Game, "g2").settlement_status == "settling"

    applied = run(db, apply=True)
    assert applied["reset"] == 1
    db.expire_all()
    assert db.get(Game, "g2").settlement_status is None

    finished = bet_service.settle_user_bets(db, user.id)
    assert finished["settled_bets"] == 1
    db.expire_all()
    assert db.get(Bet, bad.id).status == "won"
    assert db.get(Bet, bad.id).result_units == pytest.approx(3.0)
    assert db.get(Game, "g2").settlement_status == "settled"

    third = bet_service.settle_user_bets(db, user.id)
    assert third["settled_bets"] == 0
    assert db.get(Bet, good.id).result_units == pytest.approx(3.0)


def test_reset_script_is_dry_run_by_default_and_fails_closed():
    assert parse_args([]).apply is False
    assert parse_args(["--apply"]).apply is True
    with pytest.raises(SystemExit):
        guard_local_database("production", "sqlite://")
    with pytest.raises(SystemExit):
        guard_local_database("production", "postgresql://localhost/nflapp")
    refused = [
        "postgresql://user:pw@monorail.proxy.rlwy.net/db",
        "postgresql://localhost:pw@monorail.proxy.rlwy.net/db",
        "postgresql://localhost.attacker.example/db",
        "postgresql://db.internal/nfl",
        "postgresql:///",
        "",
        "not a url",
    ]
    for url in refused:
        with pytest.raises(SystemExit):
            guard_local_database("development", url)
    allowed = [
        "sqlite://",
        "sqlite:///./test.db",
        "postgresql://localhost/nflapp",
        "postgresql+psycopg://localhost/nflapp",
        "postgresql://127.0.0.1:5432/nflapp",
        "postgresql://[::1]/nflapp",
        "postgresql://postgres/nflapp",
    ]
    for url in allowed:
        guard_local_database("development", url)
        guard_local_database("test", url)
    for missing in (None, "", "   ", "production", "Production", "staging"):
        with pytest.raises(SystemExit):
            guard_local_database(missing, "sqlite://")


def test_reset_main_refuses_unset_app_env(monkeypatch):
    """Settings would default APP_ENV to development. The script must not."""
    from scripts.reset_stuck_settlements import main

    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("DATABASE_URL", "sqlite://")

    def explode(*_args, **_kwargs):
        raise AssertionError("reset script connected without APP_ENV")

    monkeypatch.setattr("app.db.SessionLocal", explode)
    with pytest.raises(SystemExit):
        main([])


def test_expired_claim_is_reclaimed_and_a_fresh_claim_is_not(db, monkeypatch):
    from app.config import Settings
    from app.services import settlement_service

    assert Settings.model_fields["settlement_claim_ttl_seconds"].default == 15 * 60
    monkeypatch.setattr(settlement_service, "_claim_ttl", lambda: timedelta(minutes=15))

    user = _user(db)
    _game(db, "g1", "PHI", "DAL", 27, 17)
    bet = _straight(db, user, "g1", "PHI", "PHI", "DAL")
    game = db.get(Game, "g1")
    fresh = datetime.now(timezone.utc) - timedelta(minutes=2)
    game.settlement_status = "settling"
    game.settlement_started_at = fresh
    game.claimed_at = fresh
    db.commit()

    warnings: list[tuple] = []

    def capture(event, **kwargs):
        warnings.append((event, kwargs))

    monkeypatch.setattr(settlement_service.log, "warning", capture)
    held = bet_service.settle_user_bets(db, user.id)
    assert held["games_stuck"] == 1
    assert held["settled_bets"] == 0
    assert warnings == []
    db.expire_all()
    assert db.get(Bet, bet.id).status == "pending"
    assert db.get(Game, "g1").settlement_status == "settling"

    game = db.get(Game, "g1")
    started = datetime.now(timezone.utc) - timedelta(minutes=20)
    game.settlement_started_at = started
    game.claimed_at = started
    db.commit()
    reclaimed = bet_service.settle_user_bets(db, user.id)
    assert reclaimed["settled_bets"] == 1
    assert reclaimed["games_stuck"] == 0
    assert warnings
    event, fields = warnings[-1]
    assert event == "settlement_claim_expired"
    assert fields["game_id"] == "g1"
    assert fields["claim_age_seconds"] >= 15 * 60
    db.expire_all()
    assert db.get(Bet, bet.id).status == "won"
    assert db.get(Bet, bet.id).result_units == pytest.approx(3.0)
    finished = db.get(Game, "g1")
    assert finished.settlement_status == "settled"
    # The first attempt's start stays put. The lease clock moves forward
    # when the expired claim is taken over.
    assert settlement_service._as_utc(finished.settlement_started_at) == settlement_service._as_utc(
        started
    )
    assert settlement_service._as_utc(finished.claimed_at) > settlement_service._as_utc(started)

    missing = _game(db, "g2", "KC", "DEN", 30, 10)
    missing.settlement_status = "settling"
    missing.claimed_at = None
    missing.settlement_started_at = started
    db.commit()
    _straight(db, user, "g2", "KC", "KC", "DEN")
    warnings.clear()
    taken = bet_service.settle_user_bets(db, user.id)
    assert taken["settled_bets"] == 1
    assert any(
        event == "settlement_claim_expired" and fields["game_id"] == "g2"
        for event, fields in warnings
    )
    assert db.get(Game, "g2").settlement_status == "settled"


def test_stale_claim_token_cannot_mark_settled(db, monkeypatch):
    """A worker whose lease was reclaimed must not commit its grades."""
    from app.services import settlement_service

    user = _user(db)
    game = _game(db, "g1", "PHI", "DAL", 27, 17)
    bet = _straight(db, user, "g1", "PHI", "PHI", "DAL")

    real = bet_service.apply_game_grades

    def reclaim_before_grading(session, graded, user_id=None):
        session.execute(
            update(Game)
            .where(Game.id == graded.id)
            .values(claim_token="reclaimed-token")
        )
        session.commit()
        return real(session, graded, user_id=user_id)

    monkeypatch.setattr(bet_service, "apply_game_grades", reclaim_before_grading)
    outcome = settlement_service.settle_game(db, game, user_id=user.id)
    assert outcome["rolled_back"] == 1
    assert outcome["settled_bets"] == 0
    db.expire_all()
    stored = db.get(Game, "g1")
    assert stored.settlement_status == "settling"
    assert stored.claim_token == "reclaimed-token"
    assert db.get(Bet, bet.id).status == "pending"
    assert db.get(Bet, bet.id).result_units is None

    # The worker that holds the new token can finish. A second call claims
    # only if the lease is expired; hand it the token directly.
    assert settlement_service.mark_settled(db, "g1", "not-the-token") is False
    db.rollback()
    assert settlement_service.mark_settled(db, "g1", "reclaimed-token") is True
    db.commit()
    db.expire_all()
    assert db.get(Game, "g1").settlement_status == "settled"
    assert db.get(Game, "g1").claim_token == "reclaimed-token"


def test_reclaimed_worker_cannot_grade_late_wagers(db, monkeypatch):
    """A settled game's late bets commit only while the observed token still matches."""
    from app.services import settlement_service

    user = _user(db)
    game = _game(db, "g1", "PHI", "DAL", 27, 17)
    first = _straight(db, user, "g1", "PHI", "PHI", "DAL")
    assert bet_service.settle_user_bets(db, user.id)["settled_bets"] == 1
    db.expire_all()
    stored = db.get(Game, "g1")
    assert stored.settlement_status == "settled"
    original = stored.claim_token
    assert original

    late = _straight(db, user, "g1", "PHI", "PHI", "DAL")
    real = bet_service.apply_game_grades

    def steal_claim(session, graded, user_id=None):
        session.execute(
            update(Game)
            .where(Game.id == graded.id)
            .values(claim_token="reclaimed-token", settlement_status="settling")
        )
        session.commit()
        return real(session, graded, user_id=user_id)

    monkeypatch.setattr(bet_service, "apply_game_grades", steal_claim)
    outcome = settlement_service.settle_game(db, game, user_id=user.id)
    assert outcome["rolled_back"] == 1
    assert outcome["settled_bets"] == 0
    db.expire_all()
    assert db.get(Bet, late.id).status == "pending"
    assert db.get(Bet, late.id).result_units is None
    assert db.get(Bet, first.id).status == "won"
    kept = db.get(Game, "g1")
    assert kept.claim_token == "reclaimed-token"
    assert kept.settlement_status == "settling"
    assert kept.claim_token != original


def test_sqlite_reclaim_uses_database_clock_for_naive_timestamps(db, monkeypatch):
    """SQLite round-trips datetimes without tzinfo. Do not compare them to an aware clock."""
    from app.services import settlement_service

    monkeypatch.setattr(settlement_service, "_claim_ttl", lambda: timedelta(minutes=15))
    game = _game(db, "g1", "PHI", "DAL", 27, 17)
    game.settlement_status = "settling"
    game.claim_token = "old-token"
    # The value SQLite will hand back: naive, no offset.
    game.claimed_at = (datetime.now(timezone.utc) - timedelta(minutes=2)).replace(tzinfo=None)
    game.settlement_started_at = game.claimed_at
    db.commit()
    db.expire_all()
    loaded = db.execute(select(Game.claimed_at).where(Game.id == "g1")).scalar()
    assert loaded.tzinfo is None

    statements: list[tuple[str, object]] = []

    def recv(conn, cursor, statement, parameters, context, executemany):
        statements.append((" ".join(statement.lower().split()), parameters))

    event.listen(db.get_bind(), "before_cursor_execute", recv)
    status, token = settlement_service.claim_game(db, db.get(Game, "g1"))
    assert status == "stuck"
    assert token is None
    updates = [sql for sql, _params in statements if sql.startswith("update")]
    assert updates
    assert "datetime('now'" in updates[0]
    assert "+00:00" not in updates[0]
    for _sql, params in statements:
        values = params.values() if isinstance(params, dict) else params
        for value in values:
            assert not (
                isinstance(value, datetime) and value.tzinfo is not None
            )
    db.expire_all()
    assert db.get(Game, "g1").claim_token == "old-token"

    stored = db.get(Game, "g1")
    stored.claimed_at = (datetime.now(timezone.utc) - timedelta(minutes=30)).replace(tzinfo=None)
    db.commit()
    status, token = settlement_service.claim_game(db, db.get(Game, "g1"))
    assert status == "claimed"
    assert token
    db.expire_all()
    assert db.get(Game, "g1").claim_token == token
    assert db.get(Game, "g1").settlement_status == "settling"


def test_regrading_a_settled_game_is_ordered_and_repeatable(db, monkeypatch):
    user = _user(db)
    game = _game(db, "g1", "PHI", "DAL", 27, 17)
    from app.services.settlement_service import settle_game

    assert settle_game(db, game)["stuck"] is False
    db.expire_all()
    assert db.get(Game, "g1").settlement_status == "settled"

    later = _straight(db, user, "g1", "PHI", "PHI", "DAL")
    earlier = _straight(db, user, "g1", "PHI", "PHI", "DAL")
    same_time = datetime(2025, 9, 8, 12, tzinfo=timezone.utc)
    # Inserted second, but placed earlier. Ordering must follow placed_at, then id.
    later.placed_at = same_time + timedelta(hours=5)
    earlier.placed_at = same_time
    db.commit()

    seen: list = []
    real = bet_service._grade_leg

    def spy(session, leg):
        seen.append(leg.bet_id)
        return real(session, leg)

    monkeypatch.setattr(bet_service, "_grade_leg", spy)
    first = bet_service.settle_user_bets(db, user.id)
    assert first["settled_bets"] == 2
    assert seen == [earlier.id, later.id]

    db.expire_all()

    def snapshot():
        rows = db.query(Bet).order_by(Bet.placed_at.asc(), Bet.id.asc()).all()
        return [
            (row.id, row.status, row.result_units, row.legs[0].leg_result)
            for row in rows
        ]

    graded = snapshot()
    assert [row[0] for row in graded] == [earlier.id, later.id]
    assert all(row[1] == "won" and row[2] == pytest.approx(3.0) for row in graded)

    second = bet_service.settle_user_bets(db, user.id)
    assert second["settled_bets"] == 0
    assert second["graded_legs"] == 0
    db.expire_all()
    assert snapshot() == graded

    kick = datetime(2025, 9, 7, 17, tzinfo=timezone.utc)
    near = _game(db, "z-near", "BUF", "MIA", 10, 7)
    far = _game(db, "a-far", "BUF", "MIA", 10, 7)
    low = _game(db, "a-tie", "GB", "CHI", 3, 3)
    high = _game(db, "b-tie", "GB", "CHI", 3, 3)
    near.start_time = kick + timedelta(minutes=10)
    far.start_time = kick + timedelta(hours=3)
    low.start_time = kick - timedelta(hours=1)
    high.start_time = kick + timedelta(hours=1)
    db.commit()
    buf = type("Leg", (), {
        "event_id": None,
        "game_id": None,
        "home_team_id": "BUF",
        "away_team_id": "MIA",
        "commence_time": kick,
    })()
    gb = type("Leg", (), {
        "event_id": None,
        "game_id": None,
        "home_team_id": "GB",
        "away_team_id": "CHI",
        "commence_time": kick,
    })()
    assert bet_service._find_game(db, buf).id == near.id
    assert bet_service._find_game(db, buf).id == near.id
    assert bet_service._find_game(db, gb).id == low.id
    assert bet_service._find_game(db, gb).id == low.id


class _Board:
    def __init__(self, events):
        self.events = events

    async def fetch_current_scoreboard(self):
        return self.events

    async def aclose(self):
        return None


def _event(event_id: str, *, home_score: int, away_score: int, status: str = "final") -> dict:
    return {
        "id": event_id,
        "season": 2025,
        "season_type": 2,
        "week": 1,
        "start_time": "2025-09-07T17:00:00Z",
        "status": status,
        "status_detail": "Final" if status == "final" else "",
        "home": {},
        "away": {},
        "home_score": home_score,
        "away_score": away_score,
        "venue": "Home",
        "broadcast": "CBS",
        "raw": {"id": event_id},
    }


def _capture_writes(engine, table: str) -> list[str]:
    found: list[str] = []

    def recv(conn, cursor, statement, parameters, context, executemany):
        sql = " ".join(statement.lower().split())
        if table in sql and sql.startswith(("insert", "update")):
            found.append(sql)

    event.listen(engine, "before_cursor_execute", recv)
    return found


def test_score_refresh_is_idempotent_and_rolls_back_one_game(db, monkeypatch):
    engine = db.get_bind()
    events = [_event("good", home_score=21, away_score=14)]
    monkeypatch.setattr(scores_service, "ESPNScoreboardAdapter", lambda: _Board(events))

    assert asyncio.run(scores_service.refresh_scoreboard(db)) == 1
    assert db.query(Game).count() == 1
    assert db.get(Game, "good").home_score == 21

    writes = _capture_writes(engine, "games")
    assert asyncio.run(scores_service.refresh_scoreboard(db)) == 1
    assert writes == []
    db.expire_all()
    assert db.query(Game).count() == 1
    assert db.get(Game, "good").home_score == 21

    game = db.get(Game, "good")
    game.settlement_status = "settled"
    db.commit()
    events[0] = _event("good", home_score=99, away_score=0)
    writes.clear()
    asyncio.run(scores_service.refresh_scoreboard(db))
    db.expire_all()
    assert db.get(Game, "good").home_score == 21
    assert db.get(Game, "good").away_score == 14
    assert writes == []

    events.append(_event("bad", home_score=1, away_score=1))
    real = scores_service._upsert_game

    def explode(session, game_id, values):
        if game_id == "bad":
            session.add(Game(id="bad", season=2025, raw={"partial": True}))
            raise RuntimeError("score write failed")
        return real(session, game_id, values)

    monkeypatch.setattr(scores_service, "_upsert_game", explode)
    assert asyncio.run(scores_service.refresh_scoreboard(db)) == 1
    db.expire_all()
    assert db.get(Game, "bad") is None
    assert db.get(Game, "good").home_score == 21


def test_schedule_refresh_does_not_insert_the_same_game_twice(db, monkeypatch):
    row = {
        "game_id": "2025_01_PHI_DAL",
        "season": 2025,
        "game_type": "REG",
        "week": 1,
        "gameday": "2025-09-07",
        "gametime": "13:00",
        "home_team": "PHI",
        "away_team": "DAL",
        "home_score": 24,
        "away_score": 20,
        "stadium": "The Linc",
        "network": "FOX",
    }

    async def schedules(_season):
        return [row, dict(row)]

    monkeypatch.setattr(scores_service._nfl_data, "schedules", schedules)
    assert asyncio.run(scores_service.refresh_season_schedule(db, 2025)) == 2
    assert db.query(Game).count() == 1
    assert db.get(Game, "2025_01_PHI_DAL").home_score == 24

    writes = _capture_writes(db.get_bind(), "games")
    assert asyncio.run(scores_service.refresh_season_schedule(db, 2025)) == 2
    assert writes == []
    assert db.query(Game).count() == 1
