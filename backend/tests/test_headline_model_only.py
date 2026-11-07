"""Headline numbers stay the model when the posted line changes.

Synthetic predictions only. No schedule download, no odds provider, no
database write.
"""
from __future__ import annotations

from app.services import market_service as ms
from app.services import predictions_service as ps
from app.services.h2h_service import _attach_displayed_market


def _v5(margin: float = 6.0, total: float = 44.0) -> dict:
    return {
        "model_version": "qb-ridge-v5",
        "margin": margin,
        "total": total,
        "margin_parts": {"epa_d": margin - 1.8, "hfa": 1.8},
        "total_parts": {},
        "features": {"qb_h": 0.0, "qb_a": 0.0},
        "uses_market_prior": True,
        "qb_change": {"home": False, "away": False},
    }


def _headline(pred: dict) -> dict:
    return {key: pred.get(key) for key in ms.HEADLINE_FIELDS}


def _assert_internally_consistent(pred: dict) -> None:
    spread = float(pred["predicted_spread"])
    total = float(pred["predicted_total"])
    home = float(pred["predicted_home_score"])
    away = float(pred["predicted_away_score"])
    assert home + away == pytest_approx(total)
    assert home - away == pytest_approx(-spread)
    if spread < -0.05:
        assert pred["home_win_prob"] > 0.5
    elif spread > 0.05:
        assert pred["home_win_prob"] < 0.5
    dist = pred["distribution"]
    assert dist["expected_margin"] == pytest_approx(-spread)
    assert dist["home_win_prob"] == pytest_approx(pred["home_win_prob"], tol=0.02)
    assert abs(pred["home_win_prob"] + pred["away_win_prob"] - 1) < 1e-9


def pytest_approx(value: float, tol: float = 0.15):
    import pytest
    return pytest.approx(value, abs=tol)


def test_v5_headline_ignores_market_line_changes():
    base = ps.predict_game(1500, 1500, v5=_v5())
    _assert_internally_consistent(base)
    assert base["model_version"] == ps.PREDICTION_MODEL_VERSION
    assert base["predicted_spread"] == pytest_approx(-6.0, tol=0.05)

    bullish = ps.predict_game(1500, 1500, v5=_v5())
    bearish = ps.predict_game(1500, 1500, v5=_v5())
    ms.attach_market_context(bullish, {
        "consensus_home_prob": 0.71,
        "spread_home": -9.5,
        "total": 51.0,
        "books": 6,
        "effective_sources": 6,
        "sources": {"sportsbooks": 6, "kalshi": False},
    })
    ms.attach_market_context(bearish, {
        "consensus_home_prob": 0.41,
        "spread_home": 3.5,
        "total": 37.5,
        "books": 1,
        "effective_sources": 1,
        "sources": {"sportsbooks": 1, "kalshi": False},
    })

    assert _headline(bullish) == _headline(bearish) == _headline(base)
    assert bullish["prediction_basis"] == "model_only"
    assert bullish["prediction_basis"] != ms.BLEND_VERSION
    assert bullish["market"]["weight"] == 0.0
    # Home favored by 9.5 in the first book, dog by 3.5 in the second.
    assert bullish["edge"]["spread"] == pytest_approx(-6.0 - (-9.5), tol=0.05)
    assert bearish["edge"]["spread"] == pytest_approx(-6.0 - 3.5, tol=0.05)
    assert bullish["edge"]["spread"] != bearish["edge"]["spread"]
    _assert_internally_consistent(bullish)


def test_pickem_missing_and_moneyline_only():
    pred = ps.predict_game(1520, 1480, v5=_v5(margin=1.0, total=43.0))
    frozen = _headline(pred)

    ms.attach_market_context(pred, {
        "consensus_home_prob": 0.5,
        "spread_home": 0,
        "total": 43.0,
        "books": 3,
    })
    assert _headline(pred) == frozen
    assert pred["market"]["spread_home"] == 0.0
    assert pred["edge"]["spread"] == pytest_approx(float(pred["predicted_spread"]), tol=0.05)

    ms.attach_market_context(pred, {
        "consensus_home_prob": None,
        "spread_home": None,
        "total": None,
        "books": 0,
    })
    assert _headline(pred) == frozen
    assert "market" not in pred
    assert pred["prediction_basis"] == "model_only"

    ms.attach_market_context(pred, {
        "consensus_home_prob": 0.66,
        "spread_home": None,
        "total": None,
        "books": 2,
    })
    assert _headline(pred) == frozen
    assert pred["edge"]["spread"] is None
    assert pred["edge"]["home_win_prob"] == pytest_approx(
        float(pred["home_win_prob"]) - 0.66, tol=0.01,
    )
    assert pred["predicted_spread"] == frozen["predicted_spread"]


def test_legacy_path_is_not_labeled_v5():
    pred = ps.predict_game(1600, 1500)
    ms.attach_market_context(pred, {
        "consensus_home_prob": 0.6,
        "spread_home": -3.0,
        "total": 45.0,
        "books": 4,
    })
    assert pred["model_version"] == ps.LEGACY_MODEL_VERSION
    assert pred["model_version"] != ps.PREDICTION_MODEL_VERSION
    assert pred["prediction_basis"] == "model_only"
    assert pred["inputs"]["v5"] is None


def test_slate_version_does_not_claim_v5_for_a_blend_or_v4():
    assert ps._slate_model_version([
        {"model_version": "qb-ridge-v5", "prediction_basis": "model_only"},
    ]) == "qb-ridge-v5"
    assert ps._slate_model_version([
        {"model_version": "qb-ridge-v5", "prediction_basis": "market-blend-v1"},
    ]) == "market-blend-v1"
    assert ps._slate_model_version([
        {"model_version": "epa-elo-ctx-v4", "prediction_basis": "model_only"},
    ]) == "epa-elo-ctx-v4"
    assert ps._slate_model_version([
        {"model_version": "qb-ridge-v5", "prediction_basis": "model_only"},
        {"model_version": "epa-elo-ctx-v4", "prediction_basis": "model_only"},
    ]) == "mixed"


def test_flatten_keeps_model_and_market_apart():
    row = ps._flatten_slate_game({
        "id": "g1",
        "home_team_id": "KC",
        "away_team_id": "BUF",
        "home_elo": 1500,
        "away_elo": 1500,
        "prediction": {
            "predicted_spread": -6.0,
            "predicted_total": 44.0,
            "predicted_home_score": 25.0,
            "predicted_away_score": 19.0,
            "home_win_prob": 0.64,
            "model_version": "qb-ridge-v5",
            "prediction_basis": "model_only",
            "model_only": {"predicted_spread": -6.0, "home_win_prob": 0.64},
            "market": {"spread_home": -2.5, "total": 47.0},
            "edge": {"spread": -3.5, "total": -3.0},
        },
    })
    assert row["model_spread"] == -6.0
    assert row["market_spread"] == -2.5
    assert row["spread_edge"] == -3.5
    assert row["model_version"] == "qb-ridge-v5"
    assert row["prediction_basis"] == "model_only"
    assert row["model_spread"] != row["market_spread"]


def test_h2h_market_attach_does_not_rewrite_headline():
    pred = {
        "home_win_prob": 0.58,
        "away_win_prob": 0.42,
        "predicted_spread": -2.5,
        "predicted_total": 46.0,
        "predicted_home_score": 24.3,
        "predicted_away_score": 21.7,
        "model_version": "epa-elo-ctx-v4",
        "distribution": {"expected_margin": 2.5, "home_win_prob": 0.58},
    }
    frozen = _headline(pred)
    _attach_displayed_market(pred, {"market": {
        "market_spread_home": -7.0,
        "market_total": 49.5,
        "market_home_win_prob": 0.72,
        "books": 5,
    }})
    assert _headline(pred) == frozen
    assert pred["model_version"] == "epa-elo-ctx-v4"
    assert pred["market"]["spread_home"] == -7.0
    assert pred["edge"]["spread"] == -2.5 - (-7.0)
    _attach_displayed_market(pred, {"market": {"market_spread_home": 0, "books": 1}})
    # A second call must not clobber an already-attached line.
    assert pred["market"]["spread_home"] == -7.0


def test_games_route_returns_model_headline(monkeypatch):
    """The public games payload is the prediction dict, not a second blend."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    pred = ps.predict_game(1500, 1500, v5=_v5(margin=4.0, total=45.0))
    ms.attach_market_context(pred, {
        "consensus_home_prob": 0.63,
        "spread_home": -7.5,
        "total": 50.0,
        "books": 5,
        "effective_sources": 5,
    })

    async def _fake(_db, _season, _week):
        return {
            "season": 2026,
            "week": 7,
            "games": [{
                "id": "synthetic",
                "season": 2026,
                "week": 7,
                "gameday": "2026-10-18",
                "home_team_id": "KC",
                "away_team_id": "BUF",
                "home_score": None,
                "away_score": None,
                "home_elo": 1500.0,
                "away_elo": 1500.0,
                "prediction": pred,
            }],
        }

    monkeypatch.setattr(ps, "predict_week", _fake)
    with TestClient(create_app()) as client:
        response = client.get("/predictions/games", params={"season": 2026, "week": 7})
    assert response.status_code == 200
    body = response.json()
    got = body["games"][0]["prediction"]
    assert got["predicted_spread"] == pred["predicted_spread"]
    assert got["predicted_total"] == pred["predicted_total"]
    assert got["home_win_prob"] == pred["home_win_prob"]
    assert got["predicted_home_score"] == pred["predicted_home_score"]
    assert got["model_version"] == "qb-ridge-v5"
    assert got["prediction_basis"] == "model_only"
    assert got["market"]["spread_home"] == -7.5
    assert got["market"]["weight"] == 0.0
    assert got["edge"]["spread"] != 0
    assert "ml_prediction" not in body["games"][0]
