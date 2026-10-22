"""Scores and bet settlement commit per game and do not double-write."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.models.bet import Bet, BetLeg
from app.models.game import Game
from app.models.odds_snapshot import OddsSnapshot
from app.models.team import Team
from app.models.user import User
from app.services import bet_service, scores_service
from scripts.reset_stuck_settlements import (
    format_report,
    guard_not_production,
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


def test_reset_script_is_dry_run_by_default_and_refuses_production():
    assert parse_args([]).apply is False
    assert parse_args(["--apply"]).apply is True
    with pytest.raises(SystemExit):
        guard_not_production("production", "sqlite://")
    with pytest.raises(SystemExit):
        guard_not_production("development", "postgresql://user:pw@monorail.proxy.rlwy.net/db")
    guard_not_production("development", "sqlite://")
    guard_not_production("development", "postgresql://localhost/nflapp")


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
