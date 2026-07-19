"""Admin override layer: CRUD, version token, application helpers, and the
require_admin gate on the /admin/overrides routes.

Follows the ``test_bets.py`` fixture style — own tables, multi-user mode so
the admin gate is actually exercised.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MULTI_USER_MODE", "true")
    from app.config import get_settings

    get_settings.cache_clear()

    from app.db import engine
    from app.models.admin_override import AdminOverride
    from app.models.user import User

    AdminOverride.__table__.drop(bind=engine, checkfirst=True)
    User.__table__.drop(bind=engine, checkfirst=True)
    User.__table__.create(bind=engine, checkfirst=True)
    AdminOverride.__table__.create(bind=engine, checkfirst=True)

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c

    AdminOverride.__table__.drop(bind=engine, checkfirst=True)
    User.__table__.drop(bind=engine, checkfirst=True)
    get_settings.cache_clear()


def _register(client: TestClient, email: str) -> dict[str, str]:
    r = client.post(
        "/auth/register",
        json={"email": email, "password": "hunter22!", "display_name": "T"},
    )
    assert r.status_code in (200, 201), r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _make_admin(email: str) -> None:
    from app.db import SessionLocal
    from app.models.user import User

    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == email).first()
        u.is_admin = True
        db.commit()
    finally:
        db.close()


def test_admin_gate_and_crud_roundtrip(client):
    admin_h = _register(client, "boss@example.com")
    _make_admin("boss@example.com")
    user_h = _register(client, "pleb@example.com")

    body = {
        "entity_type": "game",
        "entity_id": "2026_01_PHI_DAL",
        "field": "predicted_spread",
        "value": -6.5,
        "season": 2026,
        "week": 1,
        "original_value": -3.0,
        "note": "sharp injury info",
    }

    # Non-admin: 403 on every route. Anonymous: 401.
    assert client.post("/admin/overrides", json=body, headers=user_h).status_code == 403
    assert client.get("/admin/overrides", headers=user_h).status_code == 403
    assert client.get("/admin/overrides").status_code == 401

    # Admin: create → list → update (same scope upserts) → delete.
    r = client.post("/admin/overrides", json=body, headers=admin_h)
    assert r.status_code == 200, r.text
    created = r.json()
    assert created["value"] == -6.5
    assert created["original_value"] == -3.0
    assert created["created_by"] == "boss@example.com"

    r = client.post(
        "/admin/overrides",
        json={**body, "value": -7.5, "original_value": -99.0},
        headers=admin_h,
    )
    assert r.status_code == 200
    updated = r.json()
    assert updated["id"] == created["id"]  # upsert, not duplicate
    assert updated["value"] == -7.5
    assert updated["original_value"] == -3.0  # first snapshot is preserved

    r = client.get(
        "/admin/overrides?entity_type=game&season=2026&week=1", headers=admin_h,
    )
    assert r.status_code == 200
    assert len(r.json()["overrides"]) == 1

    # Bad field on a game override → 422.
    r = client.post(
        "/admin/overrides",
        json={**body, "field": "banana"},
        headers=admin_h,
    )
    assert r.status_code == 422

    r = client.delete(f"/admin/overrides/{created['id']}", headers=admin_h)
    assert r.status_code == 200
    r = client.get("/admin/overrides", headers=admin_h)
    assert r.json()["overrides"] == []


def test_version_token_changes_on_write(client):
    admin_h = _register(client, "boss2@example.com")
    _make_admin("boss2@example.com")

    from app.db import SessionLocal
    from app.services import overrides_service

    db = SessionLocal()
    try:
        v0 = overrides_service.version(db)
        client.post(
            "/admin/overrides",
            json={
                "entity_type": "player",
                "entity_id": "p1",
                "field": "receiving_yards",
                "value": 120,
                "season": 2026,
                "week": 3,
            },
            headers=admin_h,
        )
        v1 = overrides_service.version(db)
        assert v0 != v1
    finally:
        db.close()


def test_apply_game_prediction_coherence():
    from app.services import overrides_service

    pred = {
        "home_win_prob": 0.55,
        "away_win_prob": 0.45,
        "predicted_spread": -2.5,
        "predicted_total": 44.0,
        "predicted_home_score": 23.3,
        "predicted_away_score": 20.7,
        "margin_sd": 13.0,
        "distribution": {"expected_margin": 2.5, "home_win_prob": 0.55},
    }
    overrides_service.apply_game_prediction(
        pred, {"predicted_spread": -7.0, "predicted_total": 50.0},
    )
    assert pred["predicted_spread"] == -7.0
    assert pred["predicted_total"] == 50.0
    # Scores re-split around the new margin/total and stay coherent.
    assert pred["predicted_home_score"] + pred["predicted_away_score"] == pytest.approx(50.0)
    assert pred["predicted_home_score"] - pred["predicted_away_score"] == pytest.approx(7.0)
    # Win prob re-derived from the wider margin — home more likely than before.
    assert pred["home_win_prob"] > 0.55
    assert pred["home_win_prob"] + pred["away_win_prob"] == pytest.approx(1.0)
    assert pred["distribution"]["expected_margin"] == 7.0

    # Explicit prob override wins and clamps.
    overrides_service.apply_game_prediction(pred, {"home_win_prob": 0.9})
    assert pred["home_win_prob"] == 0.9
    assert pred["away_win_prob"] == pytest.approx(0.1)


def test_apply_stat_and_fantasy_overrides():
    from app.services import overrides_service

    predicted = {
        "receiving_yards": {
            "predicted": 62.0, "low": 48.0, "high": 76.0,
            "mean": 62.0, "sd": 21.0, "interval_80": [35.0, 89.0],
        },
        "receiving_tds": {
            "predicted": 0.4, "low": 0.1, "high": 0.7,
            "mean": 0.4, "sd": 0.5, "anytime_prob": 0.33,
        },
    }
    stat_means = {"receiving_yards": 62.0, "receiving_tds": 0.4}

    overrides_service.apply_player_game_overrides(
        {"receiving_yards": 95.0, "receiving_tds": 0.9, "ignored_stat": 5.0},
        predicted, stat_means,
    )
    assert predicted["receiving_yards"]["mean"] == 95.0
    assert stat_means["receiving_yards"] == 95.0
    assert predicted["receiving_yards"]["sd"] == 21.0  # spread untouched
    lo, hi = predicted["receiving_yards"]["interval_80"]
    assert lo < 95.0 < hi
    # Anytime-TD prob re-derived from the new TD mean.
    assert predicted["receiving_tds"]["anytime_prob"] > 0.33

    fantasy = {"ppr": {"mean": 14.2, "sd": 6.0, "p10": 6.5, "p90": 21.9}}
    overrides_service.apply_fantasy_overrides({"fantasy_points_ppr": 22.0}, fantasy)
    assert fantasy["ppr"]["mean"] == 22.0
    assert fantasy["ppr"]["p10"] < 22.0 < fantasy["ppr"]["p90"]


def test_apply_rank_pins():
    from app.services import overrides_service

    rows = [{"player_id": pid} for pid in ("a", "b", "c", "d", "e")]
    out = overrides_service.apply_rank_pins(rows, {"e": 1, "a": 3})
    assert [r["player_id"] for r in out] == ["e", "b", "a", "c", "d"]
    # No pins → identity.
    assert overrides_service.apply_rank_pins(rows, {}) is rows
