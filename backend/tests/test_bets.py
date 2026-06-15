"""Bet tracker: create, settle (grade + roll-up), and profile aggregation.

Runs in multi-user mode against the configured test database. Mirrors the
fixture style of ``test_auth.py`` (drops/creates the tables it needs).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MULTI_USER_MODE", "true")
    from app.config import get_settings

    get_settings.cache_clear()

    from app.db import engine
    from app.models.bet import Bet, BetLeg
    from app.models.game import Game
    from app.models.user import User

    for table in (BetLeg.__table__, Bet.__table__):
        table.drop(bind=engine, checkfirst=True)
    User.__table__.drop(bind=engine, checkfirst=True)
    User.__table__.create(bind=engine, checkfirst=True)
    Bet.__table__.create(bind=engine, checkfirst=True)
    BetLeg.__table__.create(bind=engine, checkfirst=True)
    Game.__table__.create(bind=engine, checkfirst=True)

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c

    for table in (BetLeg.__table__, Bet.__table__):
        table.drop(bind=engine, checkfirst=True)
    User.__table__.drop(bind=engine, checkfirst=True)
    get_settings.cache_clear()


def _auth(client: TestClient) -> dict[str, str]:
    r = client.post(
        "/auth/register",
        json={"email": "bettor@example.com", "password": "securepass1", "display_name": "Bettor"},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _seed_final_game(event_id: str, home_id: str, away_id: str, hs: int, as_: int) -> None:
    from app.db import SessionLocal
    from app.models.game import Game

    db = SessionLocal()
    try:
        db.merge(
            Game(
                id=event_id,
                season=2025,
                week=1,
                season_type=2,
                start_time=datetime.now(timezone.utc) - timedelta(hours=4),
                status="final",
                home_team_id=home_id,
                away_team_id=away_id,
                home_score=hs,
                away_score=as_,
            )
        )
        db.commit()
    finally:
        db.close()


def test_straight_moneyline_wins_and_profile(client: TestClient):
    h = _auth(client)
    _seed_final_game("evt1", "PHI", "DAL", 27, 17)

    bet = {
        "bet_type": "straight",
        "stake_units": 2.0,
        "stake_dollars": 100.0,
        "legs": [{
            "market": "moneyline",
            "selection": "PHI",
            "selection_label": "PHI ML",
            "odds_american": 150,
            "event_id": "evt1",
            "home_team_id": "PHI",
            "away_team_id": "DAL",
        }],
    }
    r = client.post("/bets", json=bet, headers=h)
    assert r.status_code == 201, r.text
    body = r.json()
    # PHI won outright -> bet should settle as won at +150 (decimal 2.5).
    assert body["status"] == "won"
    assert body["result_units"] == pytest.approx(3.0)        # 2u * 1.5 profit
    assert body["result_dollars"] == pytest.approx(150.0)

    prof = client.get("/bets/profile", headers=h).json()
    assert prof["won"] == 1 and prof["lost"] == 0
    assert prof["win_rate"] == 1.0
    assert prof["profit_units"] == pytest.approx(3.0)
    assert prof["roi_pct"] == pytest.approx(150.0)


def test_parlay_one_leg_loses(client: TestClient):
    h = _auth(client)
    _seed_final_game("g_win", "KC", "DEN", 30, 10)   # KC wins
    _seed_final_game("g_loss", "NYJ", "BUF", 14, 28)  # NYJ loses

    bet = {
        "bet_type": "parlay",
        "stake_units": 1.0,
        "legs": [
            {"market": "moneyline", "selection": "KC", "odds_american": -120,
             "event_id": "g_win", "home_team_id": "KC", "away_team_id": "DEN"},
            {"market": "moneyline", "selection": "NYJ", "odds_american": 110,
             "event_id": "g_loss", "home_team_id": "NYJ", "away_team_id": "BUF"},
        ],
    }
    r = client.post("/bets", json=bet, headers=h)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "lost"
    assert body["result_units"] == pytest.approx(-1.0)


def test_spread_push_and_pending_when_no_game(client: TestClient):
    h = _auth(client)
    _seed_final_game("g_push", "SF", "SEA", 20, 17)  # SF wins by 3

    # SF -3 exactly -> push.
    push_bet = {
        "bet_type": "straight", "stake_units": 1.0,
        "legs": [{"market": "spread", "selection": "SF", "line": -3.0, "odds_american": -110,
                  "event_id": "g_push", "home_team_id": "SF", "away_team_id": "SEA"}],
    }
    r = client.post("/bets", json=push_bet, headers=h)
    assert r.json()["status"] == "push"

    # No game seeded -> stays pending.
    pend_bet = {
        "bet_type": "straight", "stake_units": 1.0,
        "legs": [{"market": "total", "selection": "over", "line": 44.5, "odds_american": -110,
                  "event_id": "nope", "home_team_id": "GB", "away_team_id": "CHI"}],
    }
    r = client.post("/bets", json=pend_bet, headers=h)
    assert r.json()["status"] == "pending"


def test_validation_rejects_bad_shapes(client: TestClient):
    h = _auth(client)
    # straight with 2 legs
    r = client.post("/bets", json={
        "bet_type": "straight", "stake_units": 1.0,
        "legs": [
            {"market": "moneyline", "selection": "A", "odds_american": -110},
            {"market": "moneyline", "selection": "B", "odds_american": -110},
        ]}, headers=h)
    assert r.status_code == 422
    # spread missing line
    r = client.post("/bets", json={
        "bet_type": "straight", "stake_units": 1.0,
        "legs": [{"market": "spread", "selection": "A", "odds_american": -110}]}, headers=h)
    assert r.status_code == 422


def test_tracking_requires_account(client: TestClient):
    # No auth header in multi-user mode -> 401 from get_current_user.
    r = client.get("/bets/profile")
    assert r.status_code == 401
