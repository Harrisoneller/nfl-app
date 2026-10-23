"""Market columns stay out of the design, the published fair, and the sim inputs."""
from __future__ import annotations

from app.services import market_service as ms
from app.services import ml_predictions_service as ml
from app.services import team_ratings_v5 as v5


def test_margin_coefficients_have_no_market_term():
    assert "mkt_d" not in v5.MARGIN_COEFS
    assert "mkt_d" not in v5.MARGIN_FEATURES
    assert "mkt_d" not in v5.MARGIN_COEFS_NO_MKT
    assert not hasattr(v5, "market_ratings")


def test_ml_design_has_no_market_or_observed_weather():
    joined = " ".join(ml._FEATURES).lower()
    for banned in ("market", "spread", "moneyline", "weather", "kalshi", "adp", "implied"):
        assert banned not in joined


def test_published_fair_is_not_blended_toward_the_quote():
    pred = {
        "home_win_prob": 0.61,
        "away_win_prob": 0.39,
        "predicted_spread": -3.5,
        "predicted_total": 44.5,
        "predicted_home_score": 24.0,
        "predicted_away_score": 20.5,
        "margin_sd": 13.5,
        "distribution": {"expected_margin": 3.5},
    }
    ms.apply_market_blend(pred, {
        "consensus_home_prob": 0.80,
        "spread_home": -10.0,
        "total": 51.0,
        "books": 6,
        "effective_sources": 6.0,
    })
    assert pred["home_win_prob"] == 0.61
    assert pred["predicted_spread"] == -3.5
    assert pred["predicted_total"] == 44.5
    assert pred["distribution"]["expected_margin"] == 3.5
    assert pred["prediction_basis"] == "model_only"
    # Sportsbook spread: model (-3.5) minus the quote (-10). Positive means
    # the model has the home side less favored than the market.
    assert pred["edge"]["spread"] == 6.5
