"""Betting edge service tests."""
from unittest.mock import AsyncMock, patch

import pytest

from app.services import betting_service


def test_american_implied_negative_favorite():
    assert betting_service._american_implied(-200) == pytest.approx(0.6667, rel=1e-3)


def test_american_implied_positive_underdog():
    assert betting_service._american_implied(150) == pytest.approx(0.4, rel=1e-3)


@pytest.mark.asyncio
async def test_games_with_edge_uses_current_season():
  """Regression: season=None must not call predict_week with season=0."""
  mock_week = AsyncMock(
      return_value={"season": 2026, "week": 1, "games": [
          {
              "home_team_id": "PHI",
              "away_team_id": "WAS",
              "prediction": {"predicted_spread": -10.0, "predicted_total": 42.0, "home_win_prob": 0.8},
          },
      ]},
  )
  mock_market = AsyncMock(return_value={})
  with patch.object(betting_service.predictions_service, "predict_week", mock_week), \
       patch.object(betting_service, "_current_market_odds", mock_market), \
       patch.object(betting_service, "current_or_upcoming_season", return_value=2026):
    out = await betting_service.games_with_edge(db=None, season=None, week=None)
  mock_week.assert_awaited_once()
  assert mock_week.await_args.args[1] == 2026
  assert out["season"] == 2026
  assert len(out["games"]) == 1
