"""Holistic admin projections board — merge logic, ranks, levers, gate.

The weekly/season pipelines are exercised by their own suites; here they are
monkeypatched so the test pins the board's read-side merge: weekly + season
join, season overall/positional ranks, usage-baseline join by gsis, lever vs
stat-override split, and the require_admin gate.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MULTI_USER_MODE", "true")
    monkeypatch.setenv("ADMIN_EMAILS", "")
    from app.config import get_settings

    get_settings.cache_clear()

    from app.cache import cache
    from app.db import engine
    from app.models.admin_override import AdminOverride
    from app.models.model_param import AdminAuditLog, ModelParam, ModelParamPreset
    from app.models.user import User

    tables = (AdminOverride, ModelParam, AdminAuditLog, ModelParamPreset, User)
    for t in tables:
        t.__table__.drop(bind=engine, checkfirst=True)
    for t in (User, AdminOverride, ModelParam, AdminAuditLog, ModelParamPreset):
        t.__table__.create(bind=engine, checkfirst=True)
    cache.clear()

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c

    for t in tables:
        t.__table__.drop(bind=engine, checkfirst=True)
    cache.clear()
    get_settings.cache_clear()


def _register(client: TestClient, email: str) -> dict[str, str]:
    r = client.post(
        "/auth/register",
        json={"email": email, "password": "hunter22!", "display_name": "T"},
    )
    assert r.status_code in (200, 201), r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _make_admin(email: str) -> None:
    from app.db import SessionLocal, engine
    from app.models.user import User

    with SessionLocal() as db:
        u = db.query(User).filter(User.email == email).one()
        u.is_admin = True
        db.commit()
    engine.dispose()  # recycle any stale pooled sqlite snapshots


def _fake_weekly(pid: str, pos: str, pts: float, week: int = 3) -> dict:
    return {
        "player_id": pid, "name": f"P {pid}", "position": pos, "team": "KC",
        "week": week, "bye": False, "opponent": "LV", "is_home": True,
        "tier": "Start", "pos_rank": 1, "matchup_grade": "B+",
        "defense_factor": 1.05, "injury_multiplier": 1.0,
        "predicted": {"receiving_yards": {"mean": 74.5, "sd": 20.0}},
        "fantasy": {"ppr": {"mean": pts, "p10": pts - 6, "p90": pts + 7}},
        "game_env": {"game_script": "neutral"},
        "market": {"adp": 12.5},
    }


def _fake_season(pid: str, pos: str, total: float) -> dict:
    return {
        "player_id": pid, "gsis_id": f"00-{pid}", "name": f"P {pid}",
        "position": pos, "team": "KC", "status": "Active",
        "injury_status": None, "rookie": False, "availability": 0.92,
        "games_remaining": 17, "role": {"depth_chart_order": 1, "multiplier": 1.0},
        "input_levers": None,
        "stats": {"receiving_yards": 1150.0},
        "fantasy_ppr": {"mean": total, "p10": total - 40, "p90": total + 50,
                        "per_game": round(total / 17, 2)},
    }


@pytest.fixture
def patched_boards(monkeypatch):
    from app.services import model_inputs_service
    from app.services import player_predictions_service as proj

    async def weekly_projection_board(db, season=None, week=None, scoring="ppr", limit=400):
        return {"season": 2026, "week": 3, "players": [
            _fake_weekly("a1", "WR", 18.2),
            _fake_weekly("b2", "RB", 15.1),
        ]}

    async def projection_leaderboard(db, season=None, position=None, scoring="ppr",
                                     sort=None, limit=100):
        return {"season": 2026, "players": [
            _fake_season("a1", "WR", 285.0),   # overall #1, WR1
            _fake_season("c3", "WR", 240.0),   # overall #2, WR2 (season-only row)
            _fake_season("b2", "RB", 230.0),   # overall #3, RB1
        ]}

    async def player_usage_baselines(season):
        return {"00-a1": {"target_share": 0.27, "snap_rate": 0.88}}

    monkeypatch.setattr(proj, "weekly_projection_board", weekly_projection_board)
    monkeypatch.setattr(proj, "projection_leaderboard", projection_leaderboard)
    monkeypatch.setattr(model_inputs_service, "player_usage_baselines", player_usage_baselines)


def test_board_requires_admin(client, patched_boards):
    h = _register(client, "pleb@example.com")
    assert client.get("/admin/overrides/projections-board", headers=h).status_code == 403


def test_board_merges_week_season_ranks_and_inputs(client, patched_boards):
    h = _register(client, "boss@example.com")
    _make_admin("boss@example.com")

    # Active lever + a weekly stat override for a1.
    for body in (
        {"entity_type": "player", "entity_id": "a1", "field": "target_share",
         "value": 0.31, "season": 2026},
        {"entity_type": "player", "entity_id": "a1", "field": "receiving_yards",
         "value": 88.0, "season": 2026, "week": 3},
    ):
        assert client.post("/admin/overrides", json=body, headers=h).status_code == 200, body

    r = client.get("/admin/overrides/projections-board?season=2026", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 3
    assert body["lever_fields"] and "target_share" in body["lever_fields"]

    rows = {p["player_id"]: p for p in body["players"]}
    a1, b2, c3 = rows["a1"], rows["b2"], rows["c3"]

    # Default order = season rank.
    assert [p["player_id"] for p in body["players"]] == ["a1", "c3", "b2"]

    # Season ranks: overall + positional.
    assert a1["season"]["rank"] == 1 and a1["season"]["pos_rank"] == 1
    assert c3["season"]["rank"] == 2 and c3["season"]["pos_rank"] == 2  # WR2
    assert b2["season"]["rank"] == 3 and b2["season"]["pos_rank"] == 1  # RB1

    # Week + season cells joined on one row.
    assert a1["week"]["fantasy"] == 18.2
    assert a1["week"]["stats"]["receiving_yards"] == 74.5
    assert a1["season"]["fantasy"] == 285.0
    assert a1["season"]["stats"]["receiving_yards"] == 1150.0

    # Season-only player still appears with an empty week cell.
    assert c3["week"] == {} and c3["season"]["fantasy"] == 240.0

    # Usage baselines joined by gsis; lever override split from stat override.
    assert a1["inputs"]["baselines"]["target_share"] == 0.27
    assert a1["inputs"]["levers"] == {"target_share": 0.31}
    assert a1["override_count"] == 2
    assert len(a1["overrides"]) == 1
    assert a1["overrides"][0]["field"] == "receiving_yards"
    assert b2["inputs"]["levers"] == {} and b2["override_count"] == 0
