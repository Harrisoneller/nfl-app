"""The games endpoint leaves the ML layer off unless the caller opts in."""
import inspect

import pytest
from starlette.responses import Response

from app.routers import predictions as pred_router


def test_include_ml_parameter_defaults_off():
    default = inspect.signature(pred_router.games).parameters["include_ml"].default
    assert default is False


@pytest.mark.asyncio
async def test_games_skips_ml_unless_requested(monkeypatch):
    async def predict_week(_db, season, week):
        return {"season": season, "week": week, "games": [{"id": "g1"}]}

    async def predict_week_ml(_db, _season, _week):
        raise AssertionError("ML should not run when include_ml is left off")

    monkeypatch.setattr(pred_router.predictions_service, "predict_week", predict_week)
    monkeypatch.setattr(pred_router.ml_predictions_service, "predict_week_ml", predict_week_ml)

    body = await pred_router.games(Response(), season=2025, week=1, db=None)
    assert body["games"] == [{"id": "g1"}]
    assert body["_budget"]["ml"] is None


@pytest.mark.asyncio
async def test_games_include_ml_opt_in_still_attaches_prediction(monkeypatch):
    async def predict_week(_db, season, week):
        return {"season": season, "week": week, "games": [{"id": "g1"}]}

    async def predict_week_ml(_db, _season, _week):
        return {
            "available": True,
            "games": [{
                "game_id": "g1",
                "predicted_spread": -3.5,
                "predicted_home_margin": 3.5,
            }],
        }

    monkeypatch.setattr(pred_router.predictions_service, "predict_week", predict_week)
    monkeypatch.setattr(pred_router.ml_predictions_service, "predict_week_ml", predict_week_ml)

    body = await pred_router.games(Response(), season=2025, week=1, include_ml=True, db=None)
    assert body["games"][0]["ml_prediction"]["predicted_spread"] == -3.5
    assert body["_budget"]["ml"]["tier"] == "primary"
