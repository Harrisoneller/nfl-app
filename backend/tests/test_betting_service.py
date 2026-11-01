"""Betting edge service tests."""
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from app.services import betting_service


def test_american_implied_negative_favorite():
    assert betting_service._american_implied(-200) == pytest.approx(0.6667, rel=1e-3)


def test_positive_spread_line_is_a_home_favorite():
    """nflverse spread_line is the home margin. +4 means the home team is favored."""
    favored = pd.DataFrame([{
        "home_team": "KC", "away_team": "DET",
        "home_score": 24, "away_score": 20,
        "spread_line": 4.0, "total_line": 47.0,
    }])
    home = betting_service._record_from_subset("KC", favored)
    assert home["as_favorite"]["games"] == 1
    assert home["as_favorite"]["wins"] == 1
    assert home["as_underdog"]["games"] == 0
    # Won by 4 against a +4 line: push, not a cover.
    assert home["ats"]["pushes"] == 1
    assert home["ats"]["wins"] == 0

    dog = pd.DataFrame([{
        "home_team": "KC", "away_team": "DET",
        "home_score": 27, "away_score": 20,
        "spread_line": -3.0, "total_line": 44.0,
    }])
    home_dog = betting_service._record_from_subset("KC", dog)
    assert home_dog["as_favorite"]["games"] == 0
    assert home_dog["as_underdog"]["games"] == 1
    assert home_dog["as_underdog"]["wins"] == 1
    # Home margin +7 against a -3 line (home getting 3) covers.
    assert home_dog["ats"]["wins"] == 1

    away_fav = pd.DataFrame([{
        "home_team": "DET", "away_team": "KC",
        "home_score": 17, "away_score": 24,
        "spread_line": -3.0, "total_line": 45.0,
    }])
    away = betting_service._record_from_subset("KC", away_fav)
    assert away["as_favorite"]["games"] == 1
    assert away["as_favorite"]["wins"] == 1
    assert away["as_underdog"]["games"] == 0

    # Home favored by 7, won by 3: the home team does not cover.
    short = pd.DataFrame([{
        "home_team": "KC", "away_team": "DET",
        "home_score": 24, "away_score": 21,
        "spread_line": 7.0, "total_line": 45.0,
    }])
    missed = betting_service._record_from_subset("KC", short)
    assert missed["as_favorite"]["games"] == 1
    assert missed["ats"]["wins"] == 0
    assert missed["ats"]["losses"] == 1
    # The road dog covers that same number.
    covered = betting_service._record_from_subset("DET", short)
    assert covered["as_underdog"]["games"] == 1
    assert covered["ats"]["wins"] == 1

    pickem = pd.DataFrame([{
        "home_team": "KC", "away_team": "DET",
        "home_score": 20, "away_score": 17,
        "spread_line": 0.0, "total_line": 41.0,
    }])
    even = betting_service._record_from_subset("KC", pickem)
    assert even["as_favorite"]["games"] == 0
    assert even["as_underdog"]["games"] == 0
    other = betting_service._record_from_subset("DET", pickem)
    assert other["as_favorite"]["games"] == 0
    assert other["as_underdog"]["games"] == 0


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
