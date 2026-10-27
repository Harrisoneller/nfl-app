"""Elo backtest grades completed games instead of raising NameError.

The grading helpers were called and never defined, so any season with a
matched game 500'd /predictions/backtest/elo and the summary endpoint.
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.cache import cache
from app.deps import get_db
from app.services import backtest_service, elo_service, ml_predictions_service, prediction_dist
from app.services.backtest_service import (
    _baseline_metrics,
    _distribution_eval,
    _expected_calibration_error,
)
from app.services.uncertainty_service import calibration_score_from_ece

SEASON = 1991


class _EloRow:
    def __init__(self, team_id: str, season: int, week: int, rating: float):
        self.team_id = team_id
        self.season = season
        self.week = week
        self.rating = rating


class _Query:
    def __init__(self, rows: list[_EloRow]):
        self._rows = rows

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self._rows)


class _DB:
    def __init__(self, rows: list[_EloRow]):
        self._rows = rows

    def query(self, *_args, **_kwargs):
        return _Query(self._rows)

    def close(self):
        return None


def _schedule() -> pd.DataFrame:
    # Week 1 reads the week-0 rating. One closing line, one game without.
    return pd.DataFrame([
        {
            "home_team": "KC",
            "away_team": "DEN",
            "week": 1,
            "home_score": 24,
            "away_score": 10,
            "spread_line": -3.0,
            "location": "Home",
        },
        {
            "home_team": "BUF",
            "away_team": "NYJ",
            "week": 1,
            "home_score": 17,
            "away_score": 20,
            "spread_line": None,
            "location": "Home",
        },
    ])


def _elo_rows() -> list[_EloRow]:
    return [
        _EloRow("KC", SEASON, 0, 1600.0),
        _EloRow("DEN", SEASON, 0, 1400.0),
        _EloRow("BUF", SEASON, 0, 1550.0),
        _EloRow("NYJ", SEASON, 0, 1450.0),
    ]


def _clear_backtest_cache() -> None:
    cache.delete(f"artifact:backtest_elo:seasons:{SEASON}")
    cache.delete("artifact:backtest_elo:seasons:1987,1988,1989,1990,1991")
    cache.delete("artifact:backtest_ml:test:1991:train:1987,1988,1989,1990")


async def _schedules(season: int):
    if int(season) != SEASON:
        return pd.DataFrame()
    return _schedule()


def _install_schedule(monkeypatch) -> _DB:
    _clear_backtest_cache()
    monkeypatch.setattr(backtest_service._nfl, "schedules_df", _schedules)
    return _DB(_elo_rows())


def test_expected_calibration_error_weights_bins_and_skips_empty():
    table = [
        {"n": 50, "predicted_avg": 0.65, "actual_win_rate": 0.60},
        {"n": 50, "predicted_avg": 0.75, "actual_win_rate": 0.90},
        {"n": 0, "predicted_avg": None, "actual_win_rate": None},
    ]
    # |0.65-0.60|*50 + |0.75-0.90|*50 = 10, over 100 games.
    assert _expected_calibration_error(table) == 0.1
    assert _expected_calibration_error([]) is None
    assert _expected_calibration_error([{"n": 0, "predicted_avg": None, "actual_win_rate": None}]) is None


def test_distribution_eval_crps_and_pit_shapes():
    sigma = prediction_dist.margin_sigma()
    centered = {
        "pred_spread": -3.0,
        "actual_margin": 3.0,
    }
    one = _distribution_eval([centered])
    assert one["n"] == 1
    assert one["crps"] == pytest.approx(prediction_dist.crps_normal(3.0, sigma, 3.0), abs=1e-3)
    assert one["pit_shape"] == "insufficient"
    assert sum(b["n"] for b in one["pit_histogram"]) == 1
    assert len(one["pit_histogram"]) == 10

    # Every residual sits on a decile midpoint → a flat histogram.
    flat_rows = []
    for i in range(10):
        pit = (i + 0.5) / 10
        z = prediction_dist.norm_ppf(pit)
        for _ in range(4):
            flat_rows.append({"pred_spread": 0.0, "actual_margin": z * sigma})
    flat = _distribution_eval(flat_rows)
    assert flat["pit_shape"] == "flat"
    assert flat["n"] == 40

    wide = [{"pred_spread": 0.0, "actual_margin": 0.0} for _ in range(40)]
    assert _distribution_eval(wide)["pit_shape"] == "domed"

    narrow = []
    for i in range(40):
        actual = 40.0 if i % 2 == 0 else -40.0
        narrow.append({"pred_spread": 0.0, "actual_margin": actual})
    assert _distribution_eval(narrow)["pit_shape"] == "u_shape"

    empty = _distribution_eval([])
    assert empty["n"] == 0
    assert empty["crps"] is None
    assert empty["pit_shape"] == "insufficient"


def test_baseline_metrics_match_home_elo_and_closing_line():
    rows = [
        {
            "pred_win_prob_home": 0.80,
            "pred_spread": -7.0,
            "actual_margin": 10,
            "home_won": True,
            "closing_spread": -3.0,
        },
        {
            # Below 0.5, so Elo picks the away team and is right.
            "pred_win_prob_home": 0.30,
            "pred_spread": -4.0,
            "actual_margin": -6,
            "home_won": False,
            "closing_spread": None,
        },
    ]
    baselines = _baseline_metrics(rows)
    assert baselines["n_games"] == 2
    assert baselines["elo"]["n_games"] == 2
    assert baselines["elo"]["classifier_accuracy_pct"] == 100.0
    # Elo margins are 7 and 4; absolute errors are 3 and 10.
    assert baselines["elo"]["spread_mae"] == pytest.approx(6.5)

    assert baselines["home"]["n_games"] == 2
    assert baselines["home"]["classifier_accuracy_pct"] == 50.0
    home_margin = -elo_service.predicted_spread(
        elo_service.INITIAL_RATING, elo_service.INITIAL_RATING,
    )
    home_mae = (abs(home_margin - 10) + abs(home_margin - (-6))) / 2
    assert baselines["home"]["spread_mae"] == pytest.approx(round(home_mae, 2))

    # Closing -3 → expected margin +3. Only the first game has a line.
    assert baselines["market"]["n_games"] == 1
    assert baselines["market"]["spread_mae"] == 7.0
    assert baselines["market"]["elo_on_same_games"]["n_games"] == 1
    assert baselines["market"]["elo_on_same_games"]["spread_mae"] == 3.0
    market_p = prediction_dist.win_prob(3.0, prediction_dist.margin_sigma())
    assert baselines["market"]["log_loss"] == round(prediction_dist.log_loss(market_p, 1), 4)

    assert _baseline_metrics([])["n_games"] == 0
    assert _baseline_metrics([])["market"]["spread_mae"] is None


@pytest.mark.asyncio
async def test_backtest_elo_grades_a_game(monkeypatch):
    db = _install_schedule(monkeypatch)
    result = await backtest_service.backtest_elo(db, [SEASON])

    assert result["n_games"] == 2
    assert result["seasons"] == [SEASON]
    ece = result["overall"]["expected_calibration_error"]
    assert ece == _expected_calibration_error(result["calibration"])
    assert ece is not None
    assert 0.0 <= calibration_score_from_ece(ece) <= 1.0

    assert result["distribution"]["n"] == 2
    assert result["distribution"]["crps"] is not None
    assert sum(b["n"] for b in result["distribution"]["pit_histogram"]) == 2

    assert result["baselines"]["n_games"] == 2
    assert result["baselines"]["market"]["n_games"] == 1
    assert result["baselines"]["elo"]["spread_mae"] == result["overall"]["spread_mae"]
    assert result["per_season"][0]["n_games"] == 2


@pytest.mark.asyncio
async def test_backtest_summary_grades_a_game(monkeypatch):
    db = _install_schedule(monkeypatch)
    monkeypatch.setattr(backtest_service, "latest_completed_season", lambda: SEASON)

    async def _no_features(_db, _season):
        return None

    monkeypatch.setattr(ml_predictions_service, "_build_features_for_season", _no_features)

    summary = await backtest_service.backtest_summary(db)
    assert summary["elo"]["n_games"] == 2
    assert summary["elo"]["overall"]["expected_calibration_error"] is not None
    assert summary["elo"]["distribution"]["crps"] is not None
    assert summary["elo"]["baselines"]["home"]["n_games"] == 2
    assert "ml" in summary
    assert summary["ml"]["available"] is False


def test_backtest_elo_endpoint_grades_a_game(client, monkeypatch):
    db = _install_schedule(monkeypatch)

    def _override():
        try:
            yield db
        finally:
            db.close()

    client.app.dependency_overrides[get_db] = _override
    try:
        response = client.get(f"/predictions/backtest/elo?seasons={SEASON}")
    finally:
        client.app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    body = response.json()
    assert body["n_games"] == 2
    assert isinstance(body["overall"]["expected_calibration_error"], float)
    assert body["distribution"]["n"] == 2
    assert body["baselines"]["market"]["n_games"] == 1
    assert body["baselines"]["elo"]["n_games"] == 2
