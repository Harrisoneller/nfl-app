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
        # v5 defaults: the market carries ~80–92% (walk-forward optimal model
        # share vs closing lines was ~10%; see docs/MODEL_V5.md).
        assert ms.market_weight(1) == pytest.approx(0.82)
        assert ms.market_weight(5) == pytest.approx(0.90)
        assert ms.market_weight(50) == pytest.approx(0.92)  # cap

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


class TestAttachMarketContext:
    def test_no_market_annotates_model_only(self):
        pred = _fake_pred()
        ms.attach_market_context(pred, None)
        assert pred["prediction_basis"] == "model_only"
        assert pred["prediction_basis"] != ms.BLEND_VERSION
        assert "market" not in pred
        assert "edge" not in pred
        assert pred["home_win_prob"] == 0.55
        assert pred["model_only"]["predicted_spread"] == -1.7

    def test_alias_does_not_blend_headline(self):
        pred = _fake_pred()
        market = {
            "consensus_home_prob": 0.70, "spread_home": -6.5, "total": 48.5,
            "books": 5, "effective_sources": 5.0, "movement": None,
            "sources": {"sportsbooks": 5, "kalshi": False},
        }
        before = {k: pred[k] for k in ms.HEADLINE_FIELDS if k in pred}
        ms.apply_market_blend(pred, market)
        after = {k: pred[k] for k in ms.HEADLINE_FIELDS if k in before}
        assert after == before
        assert pred["prediction_basis"] == "model_only"
        assert pred["market"]["weight"] == 0.0
        assert pred["market"]["spread_home"] == -6.5
        assert pred["market"]["total"] == 48.5
        # Edge = model − market. Home spread is negative when home is favored.
        assert pred["model_only"]["home_win_prob"] == 0.55
        assert pred["edge"]["home_win_prob"] == pytest.approx(0.55 - 0.70, abs=1e-9)
        assert pred["edge"]["spread"] == pytest.approx(-1.7 - (-6.5))
        assert pred["edge"]["total"] == pytest.approx(44.0 - 48.5)
        # Headline scores stay the model's, not a blend of the market total.
        assert pred["predicted_home_score"] + pred["predicted_away_score"] == pytest.approx(44.0, abs=0.15)

    def test_missing_spread_is_not_invented(self):
        pred = _fake_pred()
        market = {
            "consensus_home_prob": 0.70, "spread_home": None, "total": None,
            "books": 3, "effective_sources": 3.0, "movement": None,
            "sources": {"sportsbooks": 3, "kalshi": False},
        }
        ms.attach_market_context(pred, market)
        assert pred["predicted_spread"] == -1.7
        assert pred["predicted_total"] == 44.0
        assert pred["home_win_prob"] == 0.55
        assert pred["market"]["spread_home"] is None
        assert pred["edge"]["spread"] is None
        assert pred["edge"]["total"] is None
        assert pred["edge"]["home_win_prob"] == pytest.approx(0.55 - 0.70, abs=1e-9)

    def test_pickem_and_partial_total(self):
        pred = _fake_pred()
        ms.attach_market_context(pred, {
            "consensus_home_prob": None,
            "spread_home": 0.0,
            "total": 47.0,
            "books": 2,
            "effective_sources": 2.0,
        })
        assert pred["predicted_spread"] == -1.7
        assert pred["market"]["spread_home"] == 0.0
        assert pred["market"]["consensus_home_prob"] is None
        assert pred["edge"]["spread"] == pytest.approx(-1.7)
        assert pred["edge"]["home_win_prob"] is None
        assert pred["edge"]["total"] == pytest.approx(44.0 - 47.0)

    def test_headline_invariant_when_the_line_changes(self):
        first = _fake_pred()
        second = _fake_pred()
        ms.attach_market_context(first, {
            "consensus_home_prob": 0.62, "spread_home": -3.0, "total": 41.0,
            "books": 4, "effective_sources": 4,
        })
        ms.attach_market_context(second, {
            "consensus_home_prob": 0.40, "spread_home": 6.5, "total": 54.5,
            "books": 8, "effective_sources": 8,
        })
        for key in ms.HEADLINE_FIELDS:
            assert first.get(key) == second.get(key)
        assert first["edge"]["spread"] != second["edge"]["spread"]
        assert first["market"]["spread_home"] == -3.0
        assert second["market"]["spread_home"] == 6.5
