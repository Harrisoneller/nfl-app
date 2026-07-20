"""Game-level market consensus + model↔market blending (market-aware layer v1).

The single biggest in-season signal in football is the market itself: closing
lines beat every public model, ours included. This module makes the headline
game predictions *market-aware*:

1. **Consensus** — read the persisted ``odds_lines`` snapshot (zero extra Odds
   API spend), de-vig every book's moneyline pair, and take medians across
   books for win prob, spread, and total. Optionally merge Kalshi
   prediction-market prices as an additional "book" with configurable weight.
2. **Movement** — from the append-only ``odds_snapshots`` history (Sparky's
   feed): open→current consensus home-prob delta, surfaced as context. It is
   deliberately NOT an extra nudge on the blend — the current line already
   contains the movement; nudging again would double-count it.
3. **Blend** — combine the model's numbers with consensus. Win probs blend in
   logit space (proper for probabilities), spread/total linearly. The market
   weight grows with the number of independent sources and is capped, so a
   one-book line never drowns the model and an 8-book consensus mostly is the
   forecast. The pure-model numbers are preserved under ``model_only`` and the
   difference is exposed as ``edge`` — that gap *is* the product's value-finding
   signal (spec: "treat the market as the prior and the model as the attempt
   to find where it's wrong").

All blend math is pure and unit-tested; only the consensus readers touch the DB.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from sqlalchemy.orm import Session

from ..cache import cache
from ..config import get_settings
from ..logging_config import get_logger
from ..models.odds import OddsLine
from ..models.odds_snapshot import OddsSnapshot
from ..models.seed import NFL_TEAMS
from ..utils.teams import canonical_team
from . import prediction_dist
from .sparky import odds_math

log = get_logger(__name__)

BLEND_VERSION = "market-blend-v1"

# ---- Tunables ----------------------------------------------------------------

# Market weight = min(CAP, BASE + PER_SOURCE × effective_sources). With 5 books
# the market carries ~0.80 of the headline number; with 1 book only ~0.40.
# Defaults live in param_registry ("market_blend" category) — tunable from
# /admin → Parameters without a deploy. These constants are fallback docs.
_W_BASE = 0.30
_W_PER_SOURCE = 0.10
_W_CAP = 0.85


def _p(key: str) -> float:
    from . import param_registry
    return param_registry.value(key)

# A liquid exchange price is sharper than a single retail book; count Kalshi as
# this many "books" both in the consensus average and in the blend weight.
_KALSHI_BOOK_EQUIV = 2.0

# Ignore stale lines (odds_lines is rewritten on every pull, but games already
# kicked off linger until the next pull).
_LINE_LOOKBACK_HOURS = 6.0

_CACHE_TTL = 60 * 10  # market context cache — refreshed well inside the pull cadence

# Full "Market Name" → our 3-letter id ("Kansas City Chiefs" → "KC").
_FULLNAME_TO_ID: dict[str, str] = {
    f"{t['market']} {t['name']}".lower(): t["id"] for t in NFL_TEAMS
}


def _team_id(full_name: str | None) -> str | None:
    if not full_name:
        return None
    direct = _FULLNAME_TO_ID.get(full_name.strip().lower())
    return direct or canonical_team(full_name)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---- Pure consensus math (testable without a DB) -----------------------------


def consensus_from_lines(rows: list[Any]) -> dict[tuple[str, str], dict[str, Any]]:
    """Build per-game consensus from OddsLine-shaped rows.

    Returns {(home_id, away_id): {home_prob, spread_home, total, books, event_id,
    commence_time}}. Rows only need the attributes the loop reads, so tests can
    pass simple namespaces.
    """
    # event_id → {"meta": .., "h2h": {book: {"home": price, "away": price}},
    #             "spread": {book: point}, "total": {book: point}}
    events: dict[str, dict[str, Any]] = {}
    for r in rows:
        ev = str(r.event_id or "")
        if not ev:
            continue
        slot = events.setdefault(
            ev, {"meta": r, "h2h": {}, "spread": {}, "total": {}},
        )
        book = r.bookmaker or "book"
        label = (r.label or "").strip()
        if r.market == "h2h" and r.price is not None:
            side = (
                "home" if label == (r.home_team or "").strip()
                else "away" if label == (r.away_team or "").strip()
                else None
            )
            if side:
                slot["h2h"].setdefault(book, {})[side] = r.price
        elif r.market == "spreads" and r.point is not None:
            if label == (r.home_team or "").strip():
                slot["spread"][book] = float(r.point)
        elif r.market == "totals" and r.point is not None:
            if label.lower() == "over":
                slot["total"][book] = float(r.point)

    out: dict[tuple[str, str], dict[str, Any]] = {}
    for ev, slot in events.items():
        meta = slot["meta"]
        home_id, away_id = _team_id(meta.home_team), _team_id(meta.away_team)
        if not home_id or not away_id:
            continue
        probs = [
            odds_math.devig_two_way(p["home"], p["away"])[0]
            for p in slot["h2h"].values()
            if "home" in p and "away" in p
        ]
        spreads = list(slot["spread"].values())
        totals = list(slot["total"].values())
        books = max(len(probs), len(spreads), len(totals))
        if books == 0:
            continue
        out[(home_id, away_id)] = {
            "event_id": ev,
            "home_prob": round(median(probs), 4) if probs else None,
            "spread_home": round(median(spreads), 1) if spreads else None,
            "total": round(median(totals), 1) if totals else None,
            "books": books,
            "commence_time": (
                meta.commence_time.isoformat() if meta.commence_time else None
            ),
        }
    return out


def market_weight(effective_sources: float) -> float:
    """How much of the headline number the market consensus carries."""
    if effective_sources <= 0:
        return 0.0
    return min(_p("market.w_cap"),
               _p("market.w_base") + _p("market.w_per_source") * effective_sources)


def _logit(p: float) -> float:
    p = min(1 - 1e-6, max(1e-6, p))
    return math.log(p / (1 - p))


def _expit(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def blend_prob(model_p: float, market_p: float, w_market: float) -> float:
    """Logit-space blend — respects the [0,1] geometry near the extremes."""
    return _expit((1 - w_market) * _logit(model_p) + w_market * _logit(market_p))


def blend_value(model_v: float, market_v: float, w_market: float) -> float:
    return (1 - w_market) * model_v + w_market * market_v


def merge_kalshi(
    consensus: dict[str, Any], kalshi_prob_home: float | None,
) -> tuple[float | None, float]:
    """Fold a Kalshi home prob into the sportsbook consensus.

    Returns (merged_home_prob, effective_sources). Weighted average in logit
    space: sportsbook median counts `books`, Kalshi counts _KALSHI_BOOK_EQUIV.
    """
    book_p = consensus.get("home_prob")
    books = float(consensus.get("books") or 0)
    if kalshi_prob_home is None:
        return book_p, books
    if book_p is None:
        return kalshi_prob_home, _KALSHI_BOOK_EQUIV
    n_eff = books + _KALSHI_BOOK_EQUIV
    merged = _expit(
        (books * _logit(book_p) + _KALSHI_BOOK_EQUIV * _logit(kalshi_prob_home)) / n_eff
    )
    return merged, n_eff


# ---- DB readers --------------------------------------------------------------


def _sportsbook_consensus(db: Session) -> dict[tuple[str, str], dict[str, Any]]:
    cutoff = _now() - timedelta(hours=_p("market.line_lookback_hours"))
    rows = (
        db.query(OddsLine)
        .filter(OddsLine.market.in_(("h2h", "spreads", "totals")))
        .filter(
            (OddsLine.commence_time.is_(None)) | (OddsLine.commence_time >= cutoff)
        )
        .all()
    )
    return consensus_from_lines(rows)


def _movement_context(db: Session, event_id: str) -> dict[str, Any] | None:
    """Open→latest consensus home-prob delta from the append-only snapshots."""
    rows = (
        db.query(OddsSnapshot)
        .filter(OddsSnapshot.event_id == event_id)
        .filter(OddsSnapshot.home_implied.isnot(None))
        .order_by(OddsSnapshot.captured_at.asc())
        .all()
    )
    if len(rows) < 2:
        return None
    first_t, last_t = rows[0].captured_at, rows[-1].captured_at
    if first_t == last_t:
        return None
    open_probs = [r.home_implied for r in rows if r.captured_at == first_t]
    late_probs = [r.home_implied for r in rows if r.captured_at == last_t]
    if not open_probs or not late_probs:
        return None
    open_p, late_p = median(open_probs), median(late_probs)
    return {
        "open_home_prob": round(open_p, 4),
        "latest_home_prob": round(late_p, 4),
        "delta_home_prob": round(late_p - open_p, 4),
        "snapshots": len({r.captured_at for r in rows}),
    }


async def _kalshi_probs() -> dict[frozenset[str], dict[str, float]]:
    """Cached best-effort Kalshi pull (empty when disabled/unreachable)."""
    if not get_settings().kalshi_enabled:
        return {}
    cached = cache.get("kalshi_nfl_probs")
    if cached is not None:
        return cached
    from ..adapters.data.kalshi import KalshiAdapter  # lazy: keeps import light

    adapter = KalshiAdapter()
    try:
        probs = await adapter.fetch_nfl_game_probs()
    finally:
        await adapter.aclose()
    cache.set("kalshi_nfl_probs", probs, _CACHE_TTL)
    return probs


async def week_market_context(db: Session) -> dict[tuple[str, str], dict[str, Any]]:
    """Everything predict_week needs, one consensus per upcoming game.

    {(home_id, away_id): {..consensus.., kalshi_home_prob, effective_sources,
    movement, sources}}. Cached briefly; underlying lines refresh twice daily.
    """
    cached = cache.get("week_market_context_v1")
    if cached is not None:
        return cached

    consensus = _sportsbook_consensus(db)
    kalshi = await _kalshi_probs()

    out: dict[tuple[str, str], dict[str, Any]] = {}
    for (home_id, away_id), c in consensus.items():
        k_probs = kalshi.get(frozenset((home_id, away_id)))
        k_home = (k_probs or {}).get(home_id)
        merged_prob, n_eff = merge_kalshi(c, k_home)
        out[(home_id, away_id)] = {
            **c,
            "kalshi_home_prob": round(k_home, 4) if k_home is not None else None,
            "consensus_home_prob": (
                round(merged_prob, 4) if merged_prob is not None else None
            ),
            "effective_sources": round(n_eff, 1),
            "movement": _movement_context(db, c["event_id"]),
            "sources": {
                "sportsbooks": c["books"],
                "kalshi": k_home is not None,
            },
        }
    # Kalshi-only games (books haven't posted, exchange has) still get context.
    for pair, sides in kalshi.items():
        ids = sorted(pair)
        if any(key in out for key in ((ids[0], ids[1]), (ids[1], ids[0]))):
            continue
        # Unordered pair — we don't know home/away here; predict_week does.
        out[("__kalshi__", frozenset(pair))] = {"sides": sides}  # type: ignore[index]

    cache.set("week_market_context_v1", out, _CACHE_TTL)
    return out


def context_for_game(
    ctx: dict, home_id: str, away_id: str,
) -> dict[str, Any] | None:
    """Look up a game's market context, handling the kalshi-only fallback."""
    direct = ctx.get((home_id, away_id))
    if direct:
        return direct
    k_only = ctx.get(("__kalshi__", frozenset((home_id, away_id))))
    if k_only:
        home_p = k_only["sides"].get(home_id)
        if home_p is not None:
            return {
                "event_id": None,
                "home_prob": None,
                "spread_home": None,
                "total": None,
                "books": 0,
                "commence_time": None,
                "kalshi_home_prob": round(home_p, 4),
                "consensus_home_prob": round(home_p, 4),
                "effective_sources": _p("market.kalshi_book_equiv"),
                "movement": None,
                "sources": {"sportsbooks": 0, "kalshi": True},
            }
    return None


# ---- Blend application -------------------------------------------------------


def apply_market_blend(pred: dict[str, Any], market: dict[str, Any] | None) -> None:
    """Mutate one predict_game dict: headline ← blend, model ← model_only.

    No market → annotate basis and return; the model numbers stand unchanged.
    Admin overrides run AFTER this and still supersede everything.
    """
    if not market or market.get("consensus_home_prob") is None:
        pred["prediction_basis"] = "model_only"
        return

    n_eff = float(market.get("effective_sources") or 0.0)
    w = market_weight(n_eff)
    sigma = float(pred.get("margin_sd") or prediction_dist.margin_sigma())

    model_p = float(pred["home_win_prob"])
    model_spread = float(pred["predicted_spread"])
    model_total = float(pred["predicted_total"])

    mkt_p = float(market["consensus_home_prob"])
    blended_p = blend_prob(model_p, mkt_p, w)

    mkt_spread = market.get("spread_home")
    if mkt_spread is not None:
        blended_spread = blend_value(model_spread, float(mkt_spread), w)
    else:
        # Consistency over inertia: derive the spread from the blended win prob
        # so the headline pair can't disagree about who's favored.
        # win_prob = Φ(margin/σ)  ⇒  margin = σ·Φ⁻¹(p); spread = −margin.
        blended_spread = -(sigma * prediction_dist.norm_ppf(blended_p))

    mkt_total = market.get("total")
    blended_total = (
        blend_value(model_total, float(mkt_total), w)
        if mkt_total is not None else model_total
    )

    expected_margin = -blended_spread
    m_lo80, m_hi80 = prediction_dist.margin_interval(expected_margin, sigma, 0.80)
    m_lo50, m_hi50 = prediction_dist.margin_interval(expected_margin, sigma, 0.50)

    pred["model_only"] = {
        "home_win_prob": round(model_p, 3),
        "away_win_prob": round(1 - model_p, 3),
        "predicted_spread": round(model_spread, 1),
        "predicted_total": round(model_total, 1),
    }
    pred["home_win_prob"] = round(blended_p, 3)
    pred["away_win_prob"] = round(1 - blended_p, 3)
    pred["predicted_spread"] = round(blended_spread, 1)
    pred["predicted_total"] = round(blended_total, 1)
    pred["predicted_home_score"] = round((blended_total - blended_spread) / 2, 1)
    pred["predicted_away_score"] = round((blended_total + blended_spread) / 2, 1)
    pred["distribution"] = {
        "expected_margin": round(expected_margin, 1),
        "margin_sd": sigma,
        "home_win_prob": round(blended_p, 3),
        "margin_interval_50": [round(m_lo50, 1), round(m_hi50, 1)],
        "margin_interval_80": [round(m_lo80, 1), round(m_hi80, 1)],
        "home_score_range_80": [
            round((blended_total + m_lo80) / 2, 1),
            round((blended_total + m_hi80) / 2, 1),
        ],
        "away_score_range_80": [
            round((blended_total - m_hi80) / 2, 1),
            round((blended_total - m_lo80) / 2, 1),
        ],
    }
    pred["market"] = {
        "consensus_home_prob": round(mkt_p, 4),
        "spread_home": mkt_spread,
        "total": mkt_total,
        "books": market.get("books", 0),
        "kalshi_home_prob": market.get("kalshi_home_prob"),
        "effective_sources": n_eff,
        "movement": market.get("movement"),
        "sources": market.get("sources"),
        "weight": round(w, 2),
    }
    pred["edge"] = {
        # Positive win-prob edge = the model likes HOME more than the market.
        "home_win_prob": round(model_p - mkt_p, 3),
        "spread": (
            round(model_spread - float(mkt_spread), 1)
            if mkt_spread is not None else None
        ),
        "total": (
            round(model_total - float(mkt_total), 1)
            if mkt_total is not None else None
        ),
    }
    pred["prediction_basis"] = BLEND_VERSION
