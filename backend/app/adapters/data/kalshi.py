"""Kalshi prediction-market adapter (public read-only, no auth).

Kalshi lists NFL game-winner markets under a series ticker (default
``KXNFLGAME``). Tickers look like ``KXNFLGAME-25SEP04DALPHI-PHI`` — the middle
segment encodes the matchup (date + away/home abbreviations concatenated) and
the final segment is the team the YES contract pays on. We deliberately do NOT
try to fully parse the matchup segment; instead we extract the YES-team from
the last segment and pair markets that share an event segment, which is robust
to Kalshi tweaking the date encoding.

Prices are in cents. Fair probability for a side = mid(yes_bid, yes_ask)/100,
falling back to last_price. Exchange prices carry no bookmaker vig, but a
two-sided market can still sum slightly off 1.0 — we normalize pairs when both
sides exist.

Everything here is best-effort: any network / schema surprise returns an empty
result and the market layer degrades to sportsbook-only consensus.
"""
from __future__ import annotations

from typing import Any

import httpx

from ...config import get_settings
from ...logging_config import get_logger
from ...utils.teams import canonical_team

log = get_logger(__name__)

_TIMEOUT = httpx.Timeout(6.0, connect=4.0)


class KalshiAdapter:
    """Read-only client for Kalshi's public market endpoints."""

    def __init__(self) -> None:
        s = get_settings()
        self._base = s.kalshi_api_base.rstrip("/")
        self._series = s.kalshi_nfl_series_ticker
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_TIMEOUT)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch_nfl_game_probs(self) -> dict[frozenset[str], dict[str, float]]:
        """{ {team_a, team_b} : {team_id: fair_win_prob} } for open NFL games.

        Keyed by the unordered team pair so callers can match a game without
        knowing which side Kalshi calls home.
        """
        markets = await self._fetch_markets()
        if not markets:
            return {}

        # Group YES-side probabilities by event segment.
        by_event: dict[str, dict[str, float]] = {}
        for m in markets:
            ticker = str(m.get("ticker") or "")
            parts = ticker.split("-")
            if len(parts) < 3:
                continue
            event_seg, yes_abbr = "-".join(parts[1:-1]), parts[-1]
            team = canonical_team(yes_abbr)
            prob = _fair_prob(m)
            if not team or prob is None:
                continue
            by_event.setdefault(event_seg, {})[team] = prob

        out: dict[frozenset[str], dict[str, float]] = {}
        for _seg, sides in by_event.items():
            if len(sides) != 2:
                # Single-sided event: we can't identify the opponent from the
                # YES leg alone, so skip — game matching requires the pair.
                continue
            total = sum(sides.values())
            # Coherence guard: an illiquid/placeholder market can quote both
            # sides near the same price (observed preseason: 0.75/0.74). If
            # the pair is nowhere near summing to 1 the quotes carry no
            # information — normalizing them would fabricate a 50/50.
            if not (0.75 <= total <= 1.25):
                continue
            sides = {t: p / total for t, p in sides.items()}
            (ta, pa), (tb, pb) = sides.items()
            out[frozenset((ta, tb))] = {ta: pa, tb: pb}
        return out

    async def _fetch_markets(self) -> list[dict[str, Any]]:
        try:
            markets: list[dict[str, Any]] = []
            cursor: str | None = None
            for _page in range(5):  # hard page cap — one NFL week is far smaller
                params: dict[str, Any] = {
                    "series_ticker": self._series,
                    "status": "open",
                    "limit": 200,
                }
                if cursor:
                    params["cursor"] = cursor
                r = await self._http().get(f"{self._base}/markets", params=params)
                r.raise_for_status()
                data = r.json()
                markets.extend(data.get("markets") or [])
                cursor = data.get("cursor") or None
                if not cursor:
                    break
            return markets
        except Exception as e:  # noqa: BLE001 — degrade to sportsbook-only
            log.info("kalshi_fetch_unavailable", error=str(e)[:160])
            return []


# Bid/ask wider than this → the mid is meaningless; fall back to last trade.
_MAX_SPREAD = 0.15


def _price(market: dict[str, Any], cents_key: str, dollars_key: str) -> float | None:
    """Read a price in [0,1], handling both API schemas: integer cents
    (``yes_bid: 26``) and dollar strings (``yes_bid_dollars: "0.2600"`` —
    the live schema as of 2026)."""
    v = market.get(dollars_key)
    if v is not None:
        try:
            return float(v)
        except (TypeError, ValueError):
            pass
    v = market.get(cents_key)
    if v is not None:
        try:
            return float(v) / 100.0
        except (TypeError, ValueError):
            pass
    return None


def _fair_prob(market: dict[str, Any]) -> float | None:
    """Fair probability for the YES side: tight-spread mid, else last trade."""
    bid = _price(market, "yes_bid", "yes_bid_dollars")
    ask = _price(market, "yes_ask", "yes_ask_dollars")
    p: float | None = None
    if bid is not None and ask is not None and 0 < ask <= 1 and (ask - bid) <= _MAX_SPREAD:
        p = (bid + ask) / 2.0
    else:
        p = _price(market, "last_price", "last_price_dollars")
    if p is None:
        return None
    return p if 0.01 <= p <= 0.99 else None
