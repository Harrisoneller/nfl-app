"""Market-aware game layer — consensus building + blend math (pure, no DB)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import market_service as ms
from app.services import prediction_dist


def _line(**kw) -> SimpleNamespace:
    base = dict(
        event_id="ev1", market="h2h", bookmaker="BookA",
        home_team="Kansas City Chiefs", away_team="Buffalo Bills",
        label="", price=None, point=None, commence_time=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _h2h_pair(book: str, home_price: int, away_price: int) -> list[SimpleNamespace]:
    return [
        _line(bookmaker=book, label="Kansas City Chiefs", price=home_price),
        _line(bookmaker=book, label="Buffalo Bills", price=away_price),
    ]


class TestConsensusFromLines:
    def test_devigged_median_across_books(self):
        rows = (
            _h2h_pair("A", -150, +130)
            + _h2h_pair("B", -160, +140)
            + _h2h_pair("C", -140, +120)
        )
        out = ms.consensus_from_lines(rows)
        c = out[("KC", "BUF")]
        assert c["books"] == 3
        # -150/+130 devig ≈ 0.58; consensus must be a sane favorite prob.
        assert 0.55 < c["home_prob"] < 0.62
        # De-vigged pair sums to 1 → home prob strictly between raw implieds.
        assert c["spread_home"] is None and c["total"] is None

    def test_spread_and_total_medians(self):
        rows = _h2h_pair("A", -150, +130) + [
            _line(market="spreads", bookmaker="A", label="Kansas City Chiefs", point=-3.0),
            _line(market="spreads", bookmaker="B", label="Kansas City Chiefs", point=-3.5),
            _line(market="spreads", bookmaker="C", label="Kansas City Chiefs", point=-2.5),
            _line(market="totals", bookmaker="A", label="Over", point=47.5),
            _line(market="totals", bookmaker="B", label="Over", point=48.5),
        ]
        c = ms.consensus_from_lines(rows)[("KC", "BUF")]
        assert c["spread_home"] == -3.0
        assert c["total"] == 48.0

    def test_incomplete_pairs_and_unknown_teams_skipped(self):
        rows = [
            _line(label="Kansas City Chiefs", price=-150),  # no away leg
            _line(event_id="ev2", home_team="Narnia FC", away_team="Buffalo Bills",
                  label="Narnia FC", price=-110),
        ]
        out = ms.consensus_from_lines(rows)
        assert ("KC", "BUF") not in out or out[("KC", "BUF")]["home_prob"] is None


class TestBlendMath:
    def test_weight_grows_with_sources_and_caps(self):
        assert ms.market_weight(0) == 0.0
        assert ms.market_weight(1) == pytest.approx(0.40)
        assert ms.market_weight(5) == pytest.approx(0.80)
        assert ms.market_weight(50) == pytest.approx(0.85)  # cap

    def test_blend_prob_between_inputs_and_monotone(self):
        p = ms.blend_prob(0.60, 0.70, 0.5)
        assert 0.60 < p < 0.70
        assert ms.blend_prob(0.60, 0.70, 0.9) > p  # more market weight → closer to market
        assert ms.blend_prob(0.60, 0.70, 0.0) == pytest.approx(0.60, abs=1e-6)

    def test_merge_kalshi_moves_toward_exchange(self):
        consensus = {"home_prob": 0.60, "books": 4}
        merged, n_eff = ms.merge_kalshi(consensus, 0.70)
        assert 0.60 < merged < 0.70
        assert n_eff == pytest.approx(6.0)  # 4 books + 2 kalshi-equiv
        same, n = ms.merge_kalshi(consensus, None)
        assert same == 0.60 and n == 4.0


def _fake_pred() -> dict:
    """Minimal predict_game-shaped dict."""
    return {
        "home_win_prob": 0.55, "away_win_prob": 0.45,
        "predicted_spread": -1.7, "predicted_total": 44.0,
        "predicted_home_score": 22.9, "predicted_away_score": 21.1,
        "margin_sd": prediction_dist.NFL_MARGIN_SIGMA,
        "distribution": {},
    }


class TestApplyMarketBlend:
    def test_no_market_annotates_model_only(self):
        pred = _fake_pred()
        ms.apply_market_blend(pred, None)
        assert pred["prediction_basis"] == "model_only"
        assert "market" not in pred
        assert pred["home_win_prob"] == 0.55  # untouched

    def test_blend_moves_headline_toward_market(self):
        pred = _fake_pred()
        market = {
            "consensus_home_prob": 0.70, "spread_home": -6.5, "total": 48.5,
            "books": 5, "effective_sources": 5.0, "movement": None,
            "sources": {"sportsbooks": 5, "kalshi": False},
        }
        ms.apply_market_blend(pred, market)
        assert pred["prediction_basis"] == ms.BLEND_VERSION
        assert 0.55 < pred["home_win_prob"] < 0.70
        assert -6.5 < pred["predicted_spread"] < -1.7
        assert 44.0 < pred["predicted_total"] < 48.5
        # Pure model preserved + edge = model − market.
        assert pred["model_only"]["home_win_prob"] == 0.55
        assert pred["edge"]["home_win_prob"] == pytest.approx(-0.15, abs=1e-9)
        assert pred["edge"]["spread"] == pytest.approx(4.8)
        # Scores stay internally consistent with blended spread/total.
        s = pred["predicted_home_score"] + pred["predicted_away_score"]
        assert s == pytest.approx(pred["predicted_total"], abs=0.15)
        m = pred["predicted_home_score"] - pred["predicted_away_score"]
        assert m == pytest.approx(-pred["predicted_spread"], abs=0.15)
        # Distribution re-centered on the blended margin.
        assert pred["distribution"]["expected_margin"] == pytest.approx(
            -pred["predicted_spread"], abs=0.05,
        )

    def test_missing_spread_derived_from_blended_prob(self):
        pred = _fake_pred()
        market = {
            "consensus_home_prob": 0.70, "spread_home": None, "total": None,
            "books": 3, "effective_sources": 3.0, "movement": None,
            "sources": {"sportsbooks": 3, "kalshi": False},
        }
        ms.apply_market_blend(pred, market)
        # Spread must agree in sign with the blended favorite (home).
        assert pred["home_win_prob"] > 0.5
        assert pred["predicted_spread"] < 0
        # Total absent from market → model total stands.
        assert pred["predicted_total"] == 44.0
