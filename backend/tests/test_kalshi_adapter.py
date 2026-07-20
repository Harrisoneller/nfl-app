"""Kalshi adapter parsing — validated against a live 2026 API payload."""
from __future__ import annotations

import pytest

from app.adapters.data.kalshi import _fair_prob, _price


class TestPriceSchemas:
    def test_dollar_string_schema_live_2026(self):
        # Field shapes captured from the live API (July 2026).
        m = {"yes_bid_dollars": "0.5500", "yes_ask_dollars": "0.6100"}
        assert _price(m, "yes_bid", "yes_bid_dollars") == pytest.approx(0.55)
        assert _fair_prob(m) == pytest.approx(0.58)

    def test_legacy_cent_schema(self):
        m = {"yes_bid": 55, "yes_ask": 61}
        assert _fair_prob(m) == pytest.approx(0.58)

    def test_wide_spread_falls_back_to_last_trade(self):
        # Illiquid preseason quote observed live: 0.26 / 0.74 — mid would be a
        # fabricated 0.50; last trade is the only real information.
        m = {
            "yes_bid_dollars": "0.2600", "yes_ask_dollars": "0.7400",
            "last_price_dollars": "0.7500",
        }
        assert _fair_prob(m) == pytest.approx(0.75)

    def test_no_usable_price(self):
        assert _fair_prob({}) is None
        assert _fair_prob({"yes_bid_dollars": "0.99", "yes_ask_dollars": "1.0"}) is None
