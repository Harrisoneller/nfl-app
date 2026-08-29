"""Orchestration for the rebuilt parlay engine: DB in, ranked tickets out.

Kept separate from ``sparky_service`` deliberately. That module is ~1,300 lines
of slate building, signal detection, settlement and demo backfill; the parlay
rebuild touches almost none of it and has a very different failure mode (a bug
here recommends a losing bet, a bug there shows a stale card). Isolating it
keeps the blast radius small and makes the money path independently testable.

Responsibilities
----------------
1. **Fit the trust parameters** from settled history — how much of our claimed
   edge over the market is real (``EdgeShrink``) and whether our probabilities
   are calibrated (``PlattMap``). These are the two numbers that decide whether
   anything gets recommended at all, so they are fitted from data rather than
   assumed, cached per process, and reported in every payload.
2. **Build the leg pool** for a slate: reconstruct each game's outcome
   distribution from what ``build_slate`` persisted, read spread/total prices
   out of ``odds_lines``, and hand both to the pure ``sparky.legs`` builder.
3. **Search and persist** the resulting tickets.

Data sourcing notes
-------------------
- Moneyline **prices** come from the ``odds_snapshots`` consensus that
  ``build_slate`` already stored on the prediction row; spread and total prices
  come from ``odds_lines``, because snapshots store the spread/total *points*
  but not their prices. That asymmetry is pre-existing; it is the reason
  spread/total CLV history is not yet available.
- The outcome distribution is **not recomputed** here. ``build_slate`` persists
  the model's expected margin, expected total and sigmas on the prediction row,
  and this module rebuilds a ``GameDistribution`` from those five numbers. That
  keeps the parlay page off a live model recompute, which matters given this
  app's quota history.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..logging_config import get_logger
from ..models.odds import OddsLine
from ..models.sparky import (
    SparkyGamePrediction,
    SparkyHistoricalResult,
    SparkyParlayRanking,
)
from ..utils.teams import canonical_team, is_nfl_matchup
from . import dist_model, param_registry
from .sparky import correlation, legs as leg_mod
from .sparky import parlay as parlay_mod
from .sparky import odds_math, shrinkage
from .sparky.books import is_major_book
from .sparky.odds_math import clamp
from .sparky import same_game as sgp_mod
from .sparky.legs import GameLegInputs, LegCandidate, ModelTriple, SidePrices

log = get_logger(__name__)

# Never read params at import time — this codebase's standing rule. These are
# import-safe fallbacks; live values resolve per call through `_p`.
_DEFAULTS: dict[str, float] = {
    "parlay.edge_lambda": shrinkage.DEFAULT_EDGE_LAMBDA,
    "parlay.selection_kappa": shrinkage.DEFAULT_SELECTION_KAPPA,
    "parlay.market_noise": shrinkage.DEFAULT_MARKET_NOISE,
    "parlay.rho_model": leg_mod.RHO_MODEL,
    "parlay.rho_favorite": leg_mod.RHO_FAVORITE,
    "parlay.rho_scoring": leg_mod.RHO_SCORING,
    "parlay.min_leg_edge": 0.01,
    "parlay.min_books": 3.0,
    "parlay.max_leg_price": 800.0,
    "parlay.min_leg_price": -2500.0,
    "parlay.kelly_cap": 0.02,
    "parlay.kelly_fraction": 0.25,
    "parlay.max_leg_reuse": 3.0,
    # Alternate lines: every distinct number a book actually hangs becomes a
    # leg, not just the one the most books agree on.
    "parlay.alt_lines_enabled": 1.0,
    "parlay.alt_line_max_offset": 3.5,
    "parlay.alt_line_min_books_devig": 2.0,
    "parlay.alt_lines_per_market": 4.0,
    # Same-game combinations, priced on the joint outcome lattice.
    "parlay.sgp_enabled": 1.0,
    "parlay.sgp_max_legs": 4.0,
}


def _p(key: str) -> float:
    try:
        return float(param_registry.value(key))
    except Exception:  # noqa: BLE001 — must run without a DB or registry
        return _DEFAULTS[key]


# --------------------------------------------------------------------------- #
# 1. Trust parameters, fitted from settled history
# --------------------------------------------------------------------------- #

def settled_rows(db: Session) -> list[dict[str, Any]]:
    """Settled Sparky picks in the shape the shrinkage fitters expect.

    Extracted so the value board can fit *banded* trust parameters from exactly
    the same history the parlay engine fits its global one from. Two fitters
    reading two subtly different row sets would be a silent source of
    disagreement between the two boards.
    """
    rows: list[dict[str, Any]] = []
    for r in db.query(SparkyHistoricalResult).all():
        won = r.prediction_correct
        if won is None:
            continue
        row: dict[str, Any] = {"won": bool(won)}
        wp = getattr(r, "win_prob", None)
        if wp is not None:
            row["prob"] = float(wp)
        mp = getattr(r, "model_prob", None)
        kp = getattr(r, "market_prob", None)
        if mp is not None and kp is not None:
            row["model_prob"] = float(mp)
            row["market_prob"] = float(kp)
        rows.append(row)
    return rows


_FIT_CACHE: dict[str, Any] = {"as_of": None, "shrink": None, "cal": None, "tau": None}


def fit_trust(db: Session, *, force: bool = False) -> dict[str, Any]:
    """Fit (and cache) the edge-shrinkage factor and calibration map.

    Cached against the count of settled results, so a new settlement run
    invalidates it and nothing else has to remember to.

    When there is not enough history, both fall back to conservative defaults
    and ``fitted`` is False — which the API surfaces, because "we are using a
    prior, not evidence" is exactly the kind of thing a betting product should
    not hide.
    """
    n_settled = db.query(func.count(SparkyHistoricalResult.id)).scalar() or 0
    if not force and _FIT_CACHE["as_of"] == n_settled and _FIT_CACHE["shrink"] is not None:
        return {
            "shrink": _FIT_CACHE["shrink"],
            "cal": _FIT_CACHE["cal"],
            "tau": _FIT_CACHE["tau"],
        }

    rows = settled_rows(db)

    shrink = shrinkage.fit_edge_lambda(rows)
    if not shrink.fitted:
        shrink = shrinkage.EdgeShrink(
            lam=_p("parlay.edge_lambda"), n_rows=shrink.n_rows, fitted=False,
        )
    cal = shrinkage.fit_platt(rows)
    tau = shrinkage.leg_tau(
        shrink,
        dispersion=shrinkage.edge_dispersion(rows),
        market_noise=_p("parlay.market_noise"),
    )

    _FIT_CACHE.update({"as_of": n_settled, "shrink": shrink, "cal": cal, "tau": tau})
    log.info(
        "sparky_parlay_trust_fitted",
        lam=round(shrink.lam, 4), fitted=shrink.fitted, n=shrink.n_rows,
        tau=round(tau, 4), platt_b=round(cal.b, 4),
    )
    return {"shrink": shrink, "cal": cal, "tau": tau}


# --------------------------------------------------------------------------- #
# 2. Leg pool
# --------------------------------------------------------------------------- #


def _pred_has_margin(pred: SparkyGamePrediction) -> bool:
    """True when this row already carries a usable margin centre."""
    blob = (pred.market or {}).get("dist") or {}
    if blob.get("expected_margin") is not None:
        return True
    return pred.pred_margin is not None


def _pred_has_total(pred: SparkyGamePrediction) -> bool:
    """True when this row already carries a usable total centre."""
    blob = (pred.market or {}).get("dist") or {}
    if blob.get("expected_total") is not None:
        return True
    return pred.pred_total is not None


def _pred_has_dist(pred: SparkyGamePrediction) -> bool:
    """True when this row already carries a usable (margin, total) centre."""
    return _pred_has_margin(pred) and _pred_has_total(pred)


def _distribution(
    pred: SparkyGamePrediction,
    *,
    market_margin: float | None = None,
    market_total: float | None = None,
) -> dist_model.GameDistribution | None:
    """Rebuild this game's joint outcome distribution.

    Preference order, each step only filling what the previous left None:

    1. The ``market.dist`` blob ``build_slate`` persisted (means + sigmas + rho).
    2. The ``pred_margin`` / ``pred_total`` columns on the same row.
    3. Caller-supplied market consensus (the modal spread/total from odds_lines,
       or the snapshot consensus ``build_slate`` already stored). Used only so
       a spread/total *price* can still become a clickable leg when the model
       store was cold at slate-build time. The centre sits on the market line,
       so the resulting edge is ~zero — honest, not invented value.
    """
    blob = (pred.market or {}).get("dist") or {}
    # `.get(key, default)` is wrong here: a blob that exists with
    # ``expected_margin: None`` would shadow the column fallback.
    mu_m = blob.get("expected_margin")
    if mu_m is None:
        mu_m = pred.pred_margin
    if mu_m is None:
        mu_m = market_margin
    if mu_m is None:
        spread_home = (pred.market or {}).get("spread_home")
        if spread_home is not None:
            # Snapshot convention: negative = home favoured. Home expected
            # margin is the negation.
            mu_m = -float(spread_home)

    mu_t = blob.get("expected_total")
    if mu_t is None:
        mu_t = pred.pred_total
    if mu_t is None:
        mu_t = market_total
    if mu_t is None:
        total = (pred.market or {}).get("total")
        if total is not None:
            mu_t = float(total)

    if mu_m is None and mu_t is None:
        return None
    # A spread is priceable from a margin mean alone, a total from a total
    # mean alone. Filling the missing axis with a league prior lets one
    # market survive when the other quote (or column) is absent, instead of
    # dropping both. The missing axis is inverted to a fair 0.5 later if a
    # quote exists; the prior is only there so GameDistribution can exist.
    if mu_m is None:
        mu_m = 0.0
    if mu_t is None:
        mu_t = dist_model.LEAGUE_TOTAL_ANCHOR
    sm = blob.get("margin_sd")
    st = blob.get("total_sd")
    rho = blob.get("margin_total_rho")
    if sm is None or st is None or rho is None:
        # Fall back to the model's own sigma functions rather than a flat guess;
        # they only need the two means, which we have.
        return dist_model.build_game_distribution(float(mu_m), float(mu_t))
    source = blob.get("source") or "persisted"
    return dist_model.GameDistribution(
        float(mu_m), float(mu_t), float(sm), float(st), float(rho),
        meta={"source": source},
    )


def hydrate_model_dist(db: Session, preds: list[SparkyGamePrediction]) -> int:
    """Fill missing dist blobs from ``game_predictions``, in memory.

    ``build_slate`` snapshots the model at build time. If the prediction store
    was cold then, every row is persisted without a margin/total mean, and
    spread/total legs are never constructed — even after the store warms —
    until someone rebuilds the slate. This is the cheap read that lets the
    value board and parlay cart catch up without that rebuild.

    Does not commit. Returns the number of rows hydrated.
    """
    missing = [
        p for p in preds
        if not _pred_has_dist(p) and p.home_team_id and p.away_team_id
    ]
    if not missing:
        return 0
    try:
        from ..models.game_prediction import GamePrediction
    except Exception:  # noqa: BLE001 — store is optional; market fallback still works
        return 0

    home_ids = {p.home_team_id for p in missing}
    try:
        rows = (
            db.query(GamePrediction)
            .filter(GamePrediction.home_team_id.in_(list(home_ids)))
            .all()
        )
    except Exception:  # noqa: BLE001
        log.warning("sparky_store_hydrate_failed")
        return 0

    best: dict[tuple[str, str], Any] = {}
    for r in rows:
        key = (r.home_team_id, r.away_team_id)
        prev = best.get(key)
        r_at = r.computed_at
        p_at = None if prev is None else prev.computed_at
        if r_at is not None and r_at.tzinfo is None:
            r_at = r_at.replace(tzinfo=timezone.utc)
        if p_at is not None and p_at.tzinfo is None:
            p_at = p_at.replace(tzinfo=timezone.utc)
        if prev is None or (r_at or datetime.min.replace(tzinfo=timezone.utc)) >= (
            p_at or datetime.min.replace(tzinfo=timezone.utc)
        ):
            best[key] = r

    n = 0
    for p in missing:
        row = best.get((p.home_team_id, p.away_team_id))
        if row is None:
            continue
        payload = row.payload if isinstance(row.payload, dict) else {}
        nested = payload.get("prediction") or {}
        dist = nested.get("distribution") or {}
        mu_m = dist.get("expected_margin")
        if mu_m is None:
            mu_m = row.predicted_home_margin
        if mu_m is None and row.predicted_spread is not None:
            mu_m = -float(row.predicted_spread)
        mu_t = dist.get("expected_total")
        if mu_t is None:
            mu_t = row.predicted_total
        if mu_m is None or mu_t is None:
            continue
        market = dict(p.market or {})
        market["dist"] = {
            "expected_margin": float(mu_m),
            "expected_total": float(mu_t),
            "margin_sd": dist.get("margin_sd"),
            "total_sd": dist.get("total_sd"),
            "margin_total_rho": dist.get("margin_total_rho"),
            "source": "prediction_store",
        }
        p.market = market
        if p.pred_margin is None:
            p.pred_margin = float(mu_m)
        if p.pred_total is None:
            p.pred_total = float(mu_t)
        n += 1
    if n:
        log.info("sparky_dist_hydrated_from_store", n=n, missing=len(missing))
    return n


def _odds_rows(db: Session, event_ids: list[str]) -> dict[tuple[str, str], list[OddsLine]]:
    """``(event_id, market) -> rows`` from the current odds board."""
    out: dict[tuple[str, str], list[OddsLine]] = {}
    if not event_ids:
        return out
    rows = (
        db.query(OddsLine)
        .filter(OddsLine.event_id.in_(event_ids))
        .filter(OddsLine.market.in_(("spreads", "totals")))
        .all()
    )
    for r in rows:
        if not is_major_book(r.bookmaker):
            continue
        out.setdefault((r.event_id or "", r.market), []).append(r)
    return out


def _side_prices_from_points(
    rows: list[OddsLine], label_a: str, label_b: str,
) -> SidePrices | None:
    """Prices at the **consensus** line only.

    Superseded by :func:`_lines_from_points` + :func:`_build_markets`, which
    enumerate every number a book hangs. Kept as a one-line wrapper because the
    reasoning is still load-bearing and worth stating where it is used: a leg is
    only meaningful at one specific number, so the two sides of a market must be
    de-vigged against each other **at the same line**. Pairing a -6.5 quote with
    a -7.5 quote and calling it a market invents edge out of the half point.
    """
    grouped = _lines_from_points(rows, label_a, label_b)
    modal = _modal_line(grouped)
    if modal is None:
        return None
    return _side_prices_at(modal, grouped[modal], is_alt=False)


def _same_team(a: str | None, b: str | None) -> bool:
    """True when two team strings name the same side of a market.

    Odds API outcome labels are "City Nickname"; Sparky rows sometimes store
    the same string, sometimes the nickname, sometimes a 3-letter id. Exact
    equality misses all of those, and a missed match is a vanished spread
    market — the board goes moneyline-only even though odds_lines is full.
    """
    if not a or not b:
        return False
    left, right = a.strip(), b.strip()
    if not left or not right:
        return False
    if left.casefold() == right.casefold():
        return True
    ca, cb = canonical_team(left), canonical_team(right)
    return bool(ca and cb and ca == cb)


def _spread_side(
    label: str,
    row: Any,
    home_name: str | None,
    away_name: str | None,
) -> str | None:
    """Classify a spread outcome as home (``a``) or away (``b``).

    Prefer the row's own ``home_team`` / ``away_team`` — they come from the
    same Odds API event as the outcome label, so they match when the feed is
    well-formed even if the Sparky prediction stored a shorter name.
    """
    row_home = getattr(row, "home_team", None)
    row_away = getattr(row, "away_team", None)
    for candidate in (row_home, home_name):
        if _same_team(label, candidate):
            return "a"
    for candidate in (row_away, away_name):
        if _same_team(label, candidate):
            return "b"
    return None


def _lines_from_points(
    rows: list[OddsLine], label_a: str | None, label_b: str | None,
) -> dict[float, dict[str, Any]]:
    """Group quotes by the number they are hung at.

    Returns ``line -> {a_prices, b_prices, a_books, b_books, books}``. Nothing
    is discarded here: the caller decides which lines are worth building, and
    it needs to see the whole board to decide.
    """
    def _norm(v: float | None) -> float | None:
        return None if v is None else round(float(v) * 2.0) / 2.0

    is_total = (label_a or "").strip().lower() == "over"
    out: dict[float, dict[str, Any]] = {}
    for r in rows:
        if not is_major_book(getattr(r, "bookmaker", None) or getattr(r, "book", None)):
            continue
        pt = _norm(r.point)
        if pt is None or r.price is None:
            continue
        label = (r.label or "").strip()
        if is_total:
            key = pt
            low = label.lower()
            if low.startswith("over"):
                side = "a"
            elif low.startswith("under"):
                side = "b"
            else:
                continue
        else:
            # Spreads are stored per side with opposite signs; the line that
            # identifies the market is the *home* handicap.
            side = _spread_side(label, r, label_a, label_b)
            if side is None:
                continue
            key = pt if side == "a" else -pt
        slot = out.setdefault(key, {
            "a_prices": [], "b_prices": [], "a_books": [], "b_books": [], "books": set(),
        })
        slot[f"{side}_prices"].append(int(r.price))
        slot[f"{side}_books"].append((int(r.price), r.bookmaker or ""))
        slot["books"].add(r.bookmaker or "")
    return out


def _modal_line(grouped: dict[float, dict[str, Any]]) -> float | None:
    """The number the market has settled on: most books, ties to the tightest.

    This is the only line whose de-vigged pair is treated as *the* market
    opinion. Everything else is an alternate.
    """
    best: tuple[tuple[int, float], float] | None = None
    for line, slot in grouped.items():
        if not slot["a_prices"] or not slot["b_prices"]:
            continue
        rank = (len(slot["books"]), -abs(line))
        if best is None or rank > best[0]:
            best = (rank, line)
    return None if best is None else best[1]


def _side_prices_at(
    line: float, slot: dict[str, Any], *, is_alt: bool,
) -> SidePrices:
    a_prices, b_prices = slot["a_prices"], slot["b_prices"]
    best_a = leg_mod.best_american(list(a_prices)) if a_prices else None
    best_b = leg_mod.best_american(list(b_prices)) if b_prices else None
    return SidePrices(
        consensus_a=leg_mod.median_american(list(a_prices)) if a_prices else None,
        consensus_b=leg_mod.median_american(list(b_prices)) if b_prices else None,
        best_a=best_a,
        best_b=best_b,
        best_book_a=next((bk for pr, bk in slot["a_books"] if pr == best_a), None),
        best_book_b=next((bk for pr, bk in slot["b_books"] if pr == best_b), None),
        line=line,
        n_books=len(slot["books"]),
        is_alt=is_alt,
    )


def market_implied_dist(
    gd: dist_model.GameDistribution,
    *,
    spread_line: float | None,
    spread_fair_home: float | None,
    total_line: float | None,
    total_fair_over: float | None,
) -> dist_model.GameDistribution:
    """The market's own view of the game, on our dispersion.

    Inverting the consensus spread and total into a distribution is what makes
    an alternate line priceable at all. The book has told us, precisely, where
    it thinks the centre of the margin and total distributions sit — that is
    what a line *is*. It has told us nothing about the spread of those
    distributions, so the sigmas and the margin/total correlation stay ours.

    That split is deliberate and it is the conservative one. Using our own
    *centre* at an alternate number would let the model's disagreement with the
    market count twice: once in the leg's edge, and again in the fair price the
    edge is measured against. The fair curve has to be the market's.

    **The inversion happens in the same measure the result is read back in.**
    An earlier version solved for the centre with the continuous Normal
    (``norm_ppf``) and then evaluated alternates with the discrete,
    key-number-aware PMF. Those are not the same function: at a 7.5 line, mass
    piled on the key number 7 means the discrete cover probability at
    ``mu = 7.5`` is about 0.456, not 0.5. The fair curve therefore did not even
    pass through the market's own quoted number, and every alternate line
    inherited a four-point error — enough to invent an edge on the wrong side of
    a half point, which is exactly the bet this feature exists to find.

    So the centre is solved by bisection against the *discrete* probability,
    conditional on no push (the basis the book's two prices are quoted on).
    """
    mu_m, mu_t = gd.mu_m, gd.mu_t

    if spread_line is not None and spread_fair_home is not None:
        line = float(spread_line)
        target = clamp(spread_fair_home, 1e-4, 1 - 1e-4)

        def cover_at(mu: float) -> float:
            d = dist_model.GameDistribution(mu, gd.mu_t, gd.sigma_m, gd.sigma_t, gd.rho)
            win, _push, lose = d.cover_prob_discrete(line)
            z = win + lose
            return win / z if z > 0 else 0.5

        # Bracket inside the discrete PMF's own window. `margin_pmf` integrates
        # over [-70, 70] and renormalises, so a trial centre outside that window
        # has almost all its mass truncated away and the cover probability stops
        # being monotone in mu — a bisection there silently converges on
        # nonsense. No NFL game has an expected margin near 45.
        mu_m = _solve_center(cover_at, target, gd.mu_m, lo=-45.0, hi=45.0)

    if total_line is not None and total_fair_over is not None:
        line = float(total_line)
        target = clamp(total_fair_over, 1e-4, 1 - 1e-4)

        def over_at(mu: float) -> float:
            d = dist_model.GameDistribution(mu_m, mu, gd.sigma_m, gd.sigma_t, gd.rho)
            over, _push, under = _over_under_discrete(d, line)
            z = over + under
            return over / z if z > 0 else 0.5

        mu_t = _solve_center(over_at, target, gd.mu_t, lo=3.0, hi=120.0)

    return dist_model.GameDistribution(
        mu_m, mu_t, gd.sigma_m, gd.sigma_t, gd.rho, meta={"source": "market_implied"},
    )


def _solve_center(
    f, target: float, start: float, *, lo: float, hi: float, iters: int = 60,
) -> float:
    """Bisect for the location parameter that makes ``f(mu) == target``.

    ``f`` is monotone increasing in ``mu`` over ``[lo, hi]`` — more expected
    margin, more cover probability — so bisection is exact and cannot be
    trapped. The bracket is a caller's argument rather than a multiple of sigma
    because the objective is only monotone where the underlying PMF is
    supported; see the call sites.

    If the target is not bracketed the model's own centre is returned unchanged.
    That is the honest failure: an unreachable target means the market is
    quoting something our dispersion cannot express, and inventing a centre for
    it would put a fabricated fair curve under every alternate line on the game.
    """
    f_lo, f_hi = f(lo), f(hi)
    if not (f_lo <= target <= f_hi):
        return start
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if f(mid) < target:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-6:
            break
    return 0.5 * (lo + hi)


def _spread_markets(
    gd: dist_model.GameDistribution,
    mkt: dist_model.GameDistribution,
    grouped: dict[float, dict[str, Any]],
    modal: float,
    *,
    max_offset: float,
    min_books_devig: int,
    max_alts: int,
) -> tuple[tuple[SidePrices, ModelTriple], ...]:
    return _build_markets(
        gd, mkt, grouped, modal,
        model_triple=lambda line: ModelTriple(*gd.cover_prob_discrete(line)),
        fair_triple=lambda line: mkt.cover_prob_discrete(line),
        max_offset=max_offset, min_books_devig=min_books_devig, max_alts=max_alts,
    )


def _total_markets(
    gd: dist_model.GameDistribution,
    mkt: dist_model.GameDistribution,
    grouped: dict[float, dict[str, Any]],
    modal: float,
    *,
    max_offset: float,
    min_books_devig: int,
    max_alts: int,
) -> tuple[tuple[SidePrices, ModelTriple], ...]:
    return _build_markets(
        gd, mkt, grouped, modal,
        model_triple=lambda line: ModelTriple(*_over_under_discrete(gd, line)),
        fair_triple=lambda line: _over_under_discrete(mkt, line),
        max_offset=max_offset, min_books_devig=min_books_devig, max_alts=max_alts,
    )


def _build_markets(
    gd: dist_model.GameDistribution,
    mkt: dist_model.GameDistribution,
    grouped: dict[float, dict[str, Any]],
    modal: float,
    *,
    model_triple,
    fair_triple,
    max_offset: float,
    min_books_devig: int,
    max_alts: int,
) -> tuple[tuple[SidePrices, ModelTriple], ...]:
    """One (prices, model view) pair per line worth building.

    The consensus line is always built and always de-vigged from its own quoted
    pair. An alternate line is built when a book hangs it and it is close enough
    to the consensus to be the same bet in a different place; its fair price is
    de-vigged from its own pair only when enough books quote both sides there,
    and otherwise read off the market-implied distribution. One book's two-way
    price at an off number is that book's hold, not a market.
    """
    out: list[tuple[SidePrices, ModelTriple]] = []
    ordered = sorted(grouped.items(), key=lambda kv: abs(kv[0] - modal))
    alts = 0
    for line, slot in ordered:
        has_a, has_b = bool(slot["a_prices"]), bool(slot["b_prices"])
        if not (has_a or has_b):
            continue
        is_alt = abs(line - modal) > 1e-9
        if is_alt:
            if abs(line - modal) > max_offset or alts >= max_alts:
                continue
        sp = _side_prices_at(line, slot, is_alt=is_alt)
        if not is_alt:
            if not (has_a and has_b):
                continue
        else:
            quoted_both = has_a and has_b and sp.n_books >= min_books_devig
            if not quoted_both:
                fa, fpush, fb = fair_triple(line)
                room = max(1e-9, 1.0 - fpush)
                sp = replace(
                    sp,
                    fair_a=clamp(fa / room, 1e-4, 1 - 1e-4),
                    fair_b=clamp(fb / room, 1e-4, 1 - 1e-4),
                    fair_source="derived",
                )
            alts += 1
        out.append((sp, model_triple(line)))
    return tuple(out)


def nfl_only(preds: list) -> list:
    """Keep games whose both sides resolved to an NFL team id.

    Unresolved names (Odds API vs seed mismatch) stay off the value board and
    the parlay cart rather than being priced against a dummy rating.
    """
    return [p for p in preds if is_nfl_matchup(p.home_team_id, p.away_team_id)]


# Back-compat alias so copied call sites and tests keep working during the port.
fbs_only = nfl_only


def _game_inputs(
    pred: SparkyGamePrediction, odds: dict[tuple[str, str], list[OddsLine]],
) -> GameLegInputs | None:
    if not is_nfl_matchup(pred.home_team_id, pred.away_team_id):
        return None
    market = pred.market or {}
    home_prob = market.get("home_win_prob_ensemble")
    if home_prob is None:
        home_prob = (
            pred.win_prob if pred.predicted_winner == pred.home_team_id
            else 1.0 - pred.win_prob
        )

    ml_prices = None
    if market.get("home_ml") is not None and market.get("away_ml") is not None:
        ml_prices = SidePrices(
            consensus_a=int(market["home_ml"]),
            consensus_b=int(market["away_ml"]),
            best_a=int(market["home_ml"]),
            best_b=int(market["away_ml"]),
            n_books=int(market.get("book_count") or 0),
        )

    spread_prices = model_spread = None
    total_prices = model_total = None
    spread_markets: tuple[tuple[SidePrices, ModelTriple], ...] = ()
    total_markets: tuple[tuple[SidePrices, ModelTriple], ...] = ()

    # Parse the odds board first. Team-name matching used to live behind the
    # `gd is not None` gate, so a cold prediction store (no margin/total mean)
    # silently dropped every spread and total even when odds_lines was full.
    s_grouped = _lines_from_points(
        odds.get((pred.event_id, "spreads"), []), pred.home_team, pred.away_team,
    )
    t_grouped = _lines_from_points(
        odds.get((pred.event_id, "totals"), []), "Over", "Under",
    )
    s_modal = _modal_line(s_grouped)
    t_modal = _modal_line(t_grouped)

    gd = _distribution(
        pred,
        market_margin=None if s_modal is None else -float(s_modal),
        market_total=None if t_modal is None else float(t_modal),
    )

    if gd is not None and not (_pred_has_margin(pred) and _pred_has_total(pred)):
        # Invert only the axes we don't have a model centre for. Sitting the
        # Normal on `mu = -line` does *not* make the discrete cover 50% —
        # key-number mass at 3 and 7 pulls it several points off — and that
        # gap would show up as a fake edge on every no-model spread. Invert
        # in the same measure we price in so a market-only leg is actually
        # fair at the number the books hung. A real margin with a missing
        # total must not get dragged onto the market line just because the
        # other axis had to be filled in.
        gd = market_implied_dist(
            gd,
            spread_line=s_modal if not _pred_has_margin(pred) else None,
            spread_fair_home=(
                0.5 if (not _pred_has_margin(pred) and s_modal is not None) else None
            ),
            total_line=t_modal if not _pred_has_total(pred) else None,
            total_fair_over=(
                0.5 if (not _pred_has_total(pred) and t_modal is not None) else None
            ),
        )

    if gd is not None:
        if s_modal is not None:
            spread_prices = _side_prices_at(s_modal, s_grouped[s_modal], is_alt=False)
            model_spread = ModelTriple(*gd.cover_prob_discrete(s_modal))
        if t_modal is not None:
            total_prices = _side_prices_at(t_modal, t_grouped[t_modal], is_alt=False)
            model_total = ModelTriple(*_over_under_discrete(gd, t_modal))

        if _p("parlay.alt_lines_enabled") >= 1.0:
            # The market's own centre, needed to price any number it has not
            # quoted a two-way market at.
            s_fair = t_fair = None
            if spread_prices is not None and spread_prices.complete():
                s_fair = odds_math.devig_power(
                    [spread_prices.consensus_a, spread_prices.consensus_b]
                )[0]
            if total_prices is not None and total_prices.complete():
                t_fair = odds_math.devig_power(
                    [total_prices.consensus_a, total_prices.consensus_b]
                )[0]
            mkt = market_implied_dist(
                gd,
                spread_line=s_modal, spread_fair_home=s_fair,
                total_line=t_modal, total_fair_over=t_fair,
            )
            max_offset = _p("parlay.alt_line_max_offset")
            min_books = int(_p("parlay.alt_line_min_books_devig"))
            max_alts = int(_p("parlay.alt_lines_per_market"))
            if s_modal is not None:
                spread_markets = _spread_markets(
                    gd, mkt, s_grouped, s_modal, max_offset=max_offset,
                    min_books_devig=min_books, max_alts=max_alts,
                )
            if t_modal is not None:
                total_markets = _total_markets(
                    gd, mkt, t_grouped, t_modal, max_offset=max_offset,
                    min_books_devig=min_books, max_alts=max_alts,
                )

    if ml_prices is None and spread_prices is None and total_prices is None:
        return None

    return GameLegInputs(
        event_id=pred.event_id,
        home_id=pred.home_team_id,
        away_id=pred.away_team_id,
        label=f"{pred.away_team_id} @ {pred.home_team_id}",
        ml_prices=ml_prices,
        model_home_win=float(home_prob),
        spread_prices=spread_prices,
        model_spread=model_spread,
        total_prices=total_prices,
        model_total=model_total,
        spread_markets=spread_markets,
        total_markets=total_markets,
        favorite=market.get("favorite", "home"),
    )


def _over_under_discrete(
    gd: dist_model.GameDistribution, line: float,
) -> tuple[float, float, float]:
    """``(over, push, under)`` for a total, with exact push mass at whole numbers.

    ``dist_model`` ships a discrete, key-number-aware PMF for the *margin* but
    not for the total, so this integrates the total's Normal marginal over
    integer bins. No key-number excess is layered on: NFL totals do have
    mild clustering, but this codebase has not measured it, and inventing a
    correction would put an unearned number straight into a price.
    """
    if abs(line - round(line)) > 1e-9:
        over = gd.over_prob(line)
        return over, 0.0, 1.0 - over
    k = int(round(line))
    from .prediction_dist import norm_cdf

    z_hi = (k + 0.5 - gd.mu_t) / gd.sigma_t
    z_lo = (k - 0.5 - gd.mu_t) / gd.sigma_t
    push = max(0.0, norm_cdf(z_hi) - norm_cdf(z_lo))
    over = max(0.0, 1.0 - norm_cdf(z_hi))
    under = max(0.0, norm_cdf(z_lo))
    z = over + push + under
    return (over / z, push / z, under / z) if z > 0 else (0.5, 0.0, 0.5)


def build_pool(
    db: Session,
    *,
    slate_date: date | None = None,
    event_ids: list[str] | None = None,
    markets: tuple[str, ...] | None = None,
) -> tuple[list[leg_mod.LegCandidate], dict[str, Any]]:
    """The slate's candidate leg pool, plus the meta a caller needs to explain it."""
    q = db.query(SparkyGamePrediction)
    if event_ids:
        q = q.filter(SparkyGamePrediction.event_id.in_(event_ids))
    elif slate_date is not None:
        q = q.filter(SparkyGamePrediction.slate_date == slate_date)
    preds = q.all()
    preds = fbs_only(preds)
    if not preds:
        return [], {"reason": "no predictions for this slate"}

    hydrate_model_dist(db, preds)
    trust = fit_trust(db)
    odds = _odds_rows(db, [p.event_id for p in preds])

    games: list[GameLegInputs] = []
    for p in preds:
        gi = _game_inputs(p, odds)
        if gi is not None:
            games.append(gi)

    leg_mod.RHO_MODEL = _p("parlay.rho_model")
    leg_mod.RHO_FAVORITE = _p("parlay.rho_favorite")
    leg_mod.RHO_SCORING = _p("parlay.rho_scoring")

    pool = leg_mod.build_leg_pool(
        games,
        shrink=trust["shrink"],
        cal=trust["cal"],
        tau=trust["tau"],
        markets=markets or (
            leg_mod.MARKET_MONEYLINE, leg_mod.MARKET_SPREAD, leg_mod.MARKET_TOTAL,
        ),
        min_edge=_p("parlay.min_leg_edge"),
        min_books=int(_p("parlay.min_books")),
        max_price=int(_p("parlay.max_leg_price")),
        min_price=int(_p("parlay.min_leg_price")),
    )

    by_market: dict[str, int] = {}
    for leg in pool:
        by_market[leg.market] = by_market.get(leg.market, 0) + 1

    meta = {
        "games_considered": len(games),
        "pool_size": len(pool),
        "pool_by_market": by_market,
        "trust": {
            "edge_shrink": trust["shrink"].as_dict(),
            "calibration": trust["cal"].as_dict(),
            "leg_tau": round(trust["tau"], 4),
        },
        "filters": {
            "min_leg_edge": _p("parlay.min_leg_edge"),
            "min_books": int(_p("parlay.min_books")),
            "price_range": [int(_p("parlay.min_leg_price")), int(_p("parlay.max_leg_price"))],
        },
    }
    return pool, meta


# --------------------------------------------------------------------------- #
# 3. Search + persistence
# --------------------------------------------------------------------------- #


def _search_kwargs() -> dict[str, Any]:
    return {
        "strict": True,
        "selection_kappa": _p("parlay.selection_kappa"),
        "kelly_cap": _p("parlay.kelly_cap"),
        "kelly_fraction": _p("parlay.kelly_fraction"),
        "max_leg_reuse": int(_p("parlay.max_leg_reuse")),
    }


def recommend(
    db: Session,
    *,
    slate_date: date | None = None,
    leg_counts: tuple[int, ...] = (2, 3, 4),
    top_n: int = 6,
    strict: bool = True,
    persist: bool = False,
) -> dict[str, Any]:
    """Search the whole slate for the best available tickets.

    This is what the daily "recommended parlay" should always have been. The
    previous implementation took the three highest-*confidence* games, which
    selects for games where the market and the model agree — precisely the
    games with no edge left in them.
    """
    slate_date = slate_date or datetime.now(timezone.utc).date()
    pool, meta = build_pool(db, slate_date=slate_date)
    if not pool:
        return {
            "slate_date": slate_date.isoformat(), "parlays": [], "meta": meta,
            "message": "No leg on this slate clears the minimum edge after shrinkage.",
        }

    kwargs = _search_kwargs()
    kwargs["strict"] = strict
    tickets = parlay_mod.best_parlays(pool, leg_counts=leg_counts, top_n=top_n, **kwargs)

    # When the strict gate returns nothing — which is the common case, and the
    # correct one — search again ungated so the page can still show what the
    # board actually offers, priced honestly. The two lists stay separate:
    # `parlays` is what Sparky will stand behind, `best_available` is "here is
    # the least bad ticket on this slate and here is its negative EV". Merging
    # them would turn a refusal into a recommendation, which is the failure this
    # engine was rebuilt to avoid.
    best_available: list[parlay_mod.ParlayTicket] = []
    if strict and not tickets:
        loose = dict(kwargs)
        loose["strict"] = False
        best_available = parlay_mod.best_parlays(
            pool, leg_counts=leg_counts, top_n=top_n, **loose,
        )

    if persist and tickets:
        slate_id = f"auto:{slate_date.isoformat()}"
        persist_tickets(db, slate_id, slate_date, tickets)
        db.commit()

    return {
        "slate_date": slate_date.isoformat(),
        "parlays": [t.as_dict() for t in tickets],
        "best_available": [t.as_dict() for t in best_available],
        "legs": [leg.as_dict() for leg in pool[:40]],
        "ev_summary": _ev_summary(tickets or best_available),
        "meta": meta,
        "message": None if tickets else (
            "No parlay on this slate is +EV once the edge is shrunk to what "
            "history supports and the selection penalty is applied. That is a "
            "result, not an error — but nothing is hidden: the best tickets the "
            "board actually offers are listed below with their real (negative) "
            "expected value, so you can see the size of what you would be giving up."
        ),
    }


def rank_for_events(
    db: Session,
    event_ids: list[str],
    *,
    slate_date: date | None = None,
    n_legs: int | None = None,
    top_n: int = 12,
    strict: bool = False,
    persist: bool = False,
) -> dict[str, Any]:
    """Rank tickets built from a user-chosen set of games. Never refuses.

    Two rules make this different from the recommendation board, and both exist
    because the user named these games:

    1. **No edge filter.** The board's ``min_leg_edge`` is a *search* heuristic —
       it decides which legs are worth hunting through, out of hundreds. Applied
       here it silently deletes the side of a game the user explicitly asked
       about. Worse, it used to do so *partially*: the old code fell back to an
       unfiltered pool only when the filtered pool came back completely empty,
       so picking three games where two had a qualifying leg left a pool that
       covered two of the three events. ``search_parlays`` requires one leg per
       game, found only two, and returned nothing with the message "No
       combination of these games is +EV" — which is not what happened. The pool
       here is always unfiltered.

    2. **No exception.** A game with no priceable market is a fact to report,
       not an error to raise. Raising turned the builder into an HTTP 400 that
       named no game and offered no reason.

    ``strict`` defaults to False: every combination is ranked and -EV ones are
    labelled rather than hidden. The caller asked what these games are worth;
    "nothing" is an answer, not a reason to show an empty page.
    """
    n = len(event_ids)
    if not parlay_mod.MIN_LEGS <= n <= parlay_mod.MAX_LEGS:
        raise ValueError(
            f"Parlay needs {parlay_mod.MIN_LEGS}..{parlay_mod.MAX_LEGS} games (got {n})"
        )
    if len(set(event_ids)) != n:
        raise ValueError("Parlay event_ids must be unique")

    slate_date = slate_date or datetime.now(timezone.utc).date()

    # Always unfiltered — see rule 1 above.
    pool, meta = _unfiltered_pool(db, event_ids)

    # Which of the requested games actually produced a priceable side, and why
    # not. This is the diagnostic the old ValueError swallowed.
    covered = {leg.event_id for leg in pool}
    unavailable = [
        {"event_id": e, "reason": _why_unpriceable(db, e)}
        for e in event_ids
        if e not in covered
    ]

    tickets: list[parlay_mod.ParlayTicket] = []
    if not unavailable and pool:
        kwargs = _search_kwargs()
        kwargs["strict"] = strict
        # The user named these games, so show every combination ranked rather
        # than a diversified subset — the reuse cap exists for the board.
        kwargs["max_leg_reuse"] = 10 ** 6
        tickets = parlay_mod.search_parlays(
            pool, n_legs=n_legs or n, top_n=top_n,
            require_events=event_ids, **kwargs,
        )

    slate_id = "|".join(sorted(event_ids))
    if persist and tickets:
        persist_tickets(db, slate_id, slate_date, tickets)
        db.commit()

    if unavailable:
        names = ", ".join(u["event_id"] for u in unavailable)
        message = (
            f"Could not price {len(unavailable)} of the {n} games you picked ({names}). "
            "A parlay needs one priceable side from every game, so no ticket can be "
            "built until those are available. See `unavailable_events` for why."
        )
    elif not tickets:
        message = (
            "These games could be priced, but no combination of them produced a "
            "ticket. That usually means one game has only a single quotable side."
        )
    else:
        message = None

    return {
        "slate_id": slate_id,
        "slate_date": slate_date.isoformat(),
        "parlays": [t.as_dict() for t in tickets],
        "legs": [leg.as_dict() for leg in pool],
        "unavailable_events": unavailable,
        "ev_summary": _ev_summary(tickets),
        "meta": meta,
        "message": message,
    }


def leg_menu(db: Session, event_ids: list[str]) -> dict[str, Any]:
    """Every priceable side of the named games, grouped by game.

    This is what the builder's leg picker renders. It exists because the builder
    only ever let you choose *games* — the engine then picked the market and side
    inside each one — and the game cards showed a moneyline price, so there was
    no visible sign that spreads and totals were in play at all. They always
    were; you just could not steer.

    Each game now returns more than six rows: every alternate number a book
    hangs is its own bet, and more than one row from a game may be taken, since
    same-game combinations are priced on the joint distribution rather than
    refused.

    The pool is unfiltered for the same reason ``rank_for_events`` uses an
    unfiltered pool: the user named these games, so every side stays on the menu
    and the numbers say which is worth taking.
    """
    pool, meta = _unfiltered_pool(db, event_ids)

    preds = {
        p.event_id: p
        for p in db.query(SparkyGamePrediction)
        .filter(SparkyGamePrediction.event_id.in_(event_ids))
        .all()
    }

    by_event: dict[str, list[dict[str, Any]]] = {e: [] for e in event_ids}
    for leg in pool:
        by_event.setdefault(leg.event_id, []).append(leg.as_dict())

    games = []
    for e in event_ids:
        pred = preds.get(e)
        legs = sorted(
            by_event.get(e, []),
            # Line is part of the sort now that alternates exist: without it the
            # -6.5 and -7.5 versions of a side land in arbitrary order and the
            # picker looks shuffled between refreshes.
            key=lambda d: (d["market"], d["side"], d["line"] if d["line"] is not None else 0.0),
        )
        games.append({
            "event_id": e,
            "home_team_id": pred.home_team_id if pred else None,
            "away_team_id": pred.away_team_id if pred else None,
            "home_team": pred.home_team if pred else None,
            "away_team": pred.away_team if pred else None,
            "commence_time": (
                pred.commence_time.isoformat()
                if pred is not None and pred.commence_time else None
            ),
            "legs": legs,
            "reason": None if legs else _why_unpriceable(db, e),
        })

    return {"games": games, "meta": meta}


def price_exact_ticket(
    db: Session,
    leg_keys: list[str],
    *,
    slate_date: date | None = None,
) -> dict[str, Any]:
    """Price one ticket the user built by hand, leg by leg, across any markets.

    Two things make this different from :func:`rank_for_events`.

    **No selection penalty is charged.** The winner's curse is a correction for
    *searching*. When the engine enumerates a slate and reports the best ticket,
    the winner is disproportionately the one whose estimation error happened to
    point up, so its reported EV is optimistic and must be discounted. None of
    that applies when a person names the legs — no search took place, so there
    is no order statistic to correct for, and charging it anyway makes a
    hand-built ticket look worse than it is. Per-leg edge shrinkage still
    applies, because that is a statement about how much of the model's
    disagreement with the market is real, and it is true whoever picked the leg.

    **Same-game legs are allowed, and priced differently from cross-game ones.**
    Legs sharing a game are collapsed into one unit by
    :func:`same_game.price_same_game`, which reads their dependence off the
    joint (margin, total) distribution; the resulting units are then combined by
    the cross-game factor model. Mixing markets across games — a spread here, a
    moneyline there, a total somewhere else — needs nothing special: those are
    ordinary single-leg units and always were.

    The same-game part of the payload leads with a **fair price**, not an EV.
    Books do not pay the multiplied leg prices on correlated legs; they quote a
    shaded number this app cannot see. The honest deliverable is therefore "this
    combination is worth at least +X — compare that to what your book offers",
    and ``same_game[].shade_room`` says how much shading it can absorb.
    """
    if not parlay_mod.MIN_LEGS <= len(leg_keys) <= parlay_mod.MAX_LEGS:
        raise ValueError(
            f"A parlay needs {parlay_mod.MIN_LEGS}..{parlay_mod.MAX_LEGS} legs "
            f"(got {len(leg_keys)})"
        )
    if len(set(leg_keys)) != len(leg_keys):
        raise ValueError("Duplicate legs in the ticket")

    # A leg key is "{event_id}:{market}:{side}[:{line}]" — event_id is first.
    event_ids = list({k.split(":")[0] for k in leg_keys})
    pool, meta = _unfiltered_pool(db, event_ids)
    by_key = {leg.key: leg for leg in pool}

    missing = [k for k in leg_keys if k not in by_key]
    if missing:
        raise ValueError(
            f"These legs are no longer priceable (the line or price moved): {missing}"
        )

    legs = [by_key[k] for k in leg_keys]

    # Group by game, preserving the order the user built the ticket in.
    grouped: dict[str, list[LegCandidate]] = {}
    for leg in legs:
        grouped.setdefault(leg.event_id, []).append(leg)

    sgp_on = _p("parlay.sgp_enabled") >= 1.0
    sgp_max = int(_p("parlay.sgp_max_legs"))
    if not sgp_on and any(len(v) > 1 for v in grouped.values()):
        raise ValueError(
            "Same-game legs are turned off (parlay.sgp_enabled). Every leg must "
            "come from a different game."
        )

    dists = _distributions_for(db, [e for e, v in grouped.items() if len(v) > 1])

    units: list[correlation.PricedUnit] = []
    quotes: list[sgp_mod.SameGameQuote] = []
    for event_id, group in grouped.items():
        if len(group) == 1:
            units.append(correlation.to_unit(parlay_mod.to_priced(group[0])))
            continue
        gd = dists.get(event_id)
        if gd is None:
            raise ValueError(
                f"{group[0].label.split()[0]}: two legs from this game need its "
                f"outcome distribution to be priced together, and the prediction "
                f"store has no distribution for it. Pick one leg from this game, "
                f"or rebuild the slate."
            )
        try:
            quote = sgp_mod.price_same_game(gd, group, max_legs=sgp_max)
        except sgp_mod.SameGameError as e:
            raise ValueError(str(e)) from None
        quotes.append(quote)
        units.append(quote.unit)

    fast = correlation.price_units(units)
    pricing = correlation.price_units(units, exact=True)

    ev = pricing.expected_value          # no selection penalty — see docstring
    dec = pricing.decimal_odds
    kelly = odds_math.kelly_from_moments(
        1.0 + ev, pricing.return_sd,
        cap=_p("parlay.kelly_cap"), fraction=_p("parlay.kelly_fraction"),
    )
    ticket = parlay_mod.ParlayTicket(
        rank=1,
        legs=legs,
        pricing=pricing,
        selection_penalty=0.0,
        ev_adjusted=ev,
        kelly_fraction=kelly,
        growth_rate=parlay_mod.growth_rate(ev, pricing.return_sd),
        parlay_odds_american=odds_math.decimal_to_american(dec),
        parlay_odds_decimal=dec,
        implied_prob=odds_math.decimal_to_implied(dec),
        is_value=ev > 0,
        exact_priced=True,
        warnings=parlay_mod._warnings(tuple(legs), pricing, fast) + _sgp_warnings(quotes),
    )
    ticket.explanation = parlay_mod.explain(ticket)

    return {
        "slate_id": "|".join(sorted(leg_keys)),
        "slate_date": (slate_date or datetime.now(timezone.utc).date()).isoformat(),
        "parlays": [ticket.as_dict()],
        "legs": [leg.as_dict() for leg in legs],
        "same_game": [q.as_dict() for q in quotes],
        "unavailable_events": [],
        "ev_summary": _ev_summary([ticket]),
        "meta": {
            **meta,
            "hand_built": True,
            "selection_penalty_applied": False,
            "units": len(units),
            "same_game_units": len(quotes),
        },
        "message": _hand_built_message(ev, quotes),
    }


def _distributions_for(
    db: Session, event_ids: list[str],
) -> dict[str, dist_model.GameDistribution]:
    """Outcome distributions for the games that need same-game pricing."""
    if not event_ids:
        return {}
    out: dict[str, dist_model.GameDistribution] = {}
    preds = (
        db.query(SparkyGamePrediction)
        .filter(SparkyGamePrediction.event_id.in_(event_ids))
        .all()
    )
    hydrate_model_dist(db, preds)
    for pred in preds:
        gd = _distribution(pred)
        if gd is not None:
            out[pred.event_id] = gd
    return out


def _sgp_warnings(quotes: list[sgp_mod.SameGameQuote]) -> list[str]:
    out: list[str] = []
    for q in quotes:
        out.extend(q.notes)
        if q.correlation_effect < -0.02:
            out.append(
                "the legs in one game partly cancel: the combination is worth "
                "less than multiplying their prices suggests"
            )
    return out


def _hand_built_message(ev: float, quotes: list[sgp_mod.SameGameQuote]) -> str | None:
    if quotes:
        # For a same-game ticket the EV against the multiplied price is a
        # hypothetical, so it must not be reported as though the bet existed at
        # that number. Lead with the fair price instead.
        parts = [
            f"{'+' if q.fair_american > 0 else ''}{q.fair_american} "
            f"({q.shade_room:.0%} of shading absorbed)"
            for q in quotes
        ]
        return (
            "This ticket has same-game legs. Books reprice those rather than "
            "multiplying the leg prices, and we cannot see their number — so "
            "the figure to use is the fair price: "
            + "; ".join(parts)
            + ". Take it only if your book pays more than that."
        )
    if ev > 0:
        return None
    return (
        "This ticket is -EV at these prices. Nothing is being hidden — the "
        "number is what the combination is actually worth, and no selection "
        "penalty was charged because you picked the legs yourself."
    )


def _why_unpriceable(db: Session, event_id: str) -> str:
    """Plain-English reason a requested game produced no leg.

    Every branch here is a real state this codebase reaches, and they need
    different fixes — a cold prediction store is not the same problem as a
    missing odds pull, and telling someone the wrong one costs them an hour.
    """
    pred = (
        db.query(SparkyGamePrediction)
        .filter(SparkyGamePrediction.event_id == event_id)
        .first()
    )
    if pred is None:
        return "no prediction row for this event on the current slate"

    market = pred.market or {}
    has_ml = market.get("home_ml") is not None and market.get("away_ml") is not None
    blob = market.get("dist") or {}
    has_dist = (
        blob.get("expected_margin") is not None and blob.get("expected_total") is not None
    ) or (pred.pred_margin is not None and pred.pred_total is not None)

    n_odds = (
        db.query(OddsLine)
        .filter(OddsLine.event_id == event_id)
        .filter(OddsLine.market.in_(("spreads", "totals")))
        .count()
    )

    if not has_ml and n_odds == 0:
        return (
            "no book prices at all for this game — the odds board has no moneyline, "
            "spread or total for it. Run an odds refresh."
        )
    if not has_dist and not has_ml:
        return (
            "no model distribution and no moneyline consensus, so nothing can be "
            "priced. Warm the prediction store and rebuild the slate."
        )
    if not has_dist and n_odds > 0:
        return (
            "spread and total prices exist but none could be matched to this "
            "game's team names, so no spread/total leg was built. Check "
            "odds_lines labels against the prediction row's home_team/away_team."
        )
    return (
        "priced markets exist but none survived the price sanity limits "
        "(quotes outside the allowed range, or fewer books than required)."
    )


def _ev_summary(tickets: list[parlay_mod.ParlayTicket]) -> dict[str, Any]:
    """Headline EV numbers for whatever was built, +EV or not.

    Returned even when every ticket is negative. A builder that only reports EV
    when the news is good is not reporting EV.
    """
    if not tickets:
        return {"count": 0, "best_ev": None, "any_positive": False}
    best = max(tickets, key=lambda t: t.ev_adjusted)
    return {
        "count": len(tickets),
        "best_ev": round(best.ev_adjusted, 4),
        "best_ev_pct": round(best.ev_adjusted * 100.0, 2),
        "best_rank": best.rank,
        "worst_ev_pct": round(min(t.ev_adjusted for t in tickets) * 100.0, 2),
        "any_positive": any(t.ev_adjusted > 0 for t in tickets),
        "positive_count": sum(1 for t in tickets if t.ev_adjusted > 0),
    }


def _unfiltered_pool(
    db: Session, event_ids: list[str],
) -> tuple[list[leg_mod.LegCandidate], dict[str, Any]]:
    """Pool with the edge filter dropped — used when the user names the games."""
    preds = (
        db.query(SparkyGamePrediction)
        .filter(SparkyGamePrediction.event_id.in_(event_ids))
        .all()
    )
    preds = fbs_only(preds)
    if not preds:
        return [], {"reason": "no predictions for these events"}
    hydrate_model_dist(db, preds)
    trust = fit_trust(db)
    odds = _odds_rows(db, [p.event_id for p in preds])
    games = [gi for gi in (_game_inputs(p, odds) for p in preds) if gi is not None]
    pool = leg_mod.build_leg_pool(
        games, shrink=trust["shrink"], cal=trust["cal"], tau=trust["tau"],
        min_edge=-1.0, min_books=1,
        max_price=int(_p("parlay.max_leg_price")),
        min_price=int(_p("parlay.min_leg_price")),
    )
    return pool, {
        "games_considered": len(games),
        "pool_size": len(pool),
        "unfiltered": True,
        "trust": {
            "edge_shrink": trust["shrink"].as_dict(),
            "calibration": trust["cal"].as_dict(),
            "leg_tau": round(trust["tau"], 4),
        },
    }


def persist_tickets(
    db: Session, slate_id: str, slate_date: date, tickets: list[parlay_mod.ParlayTicket],
) -> None:
    """Replace this slate's persisted rankings with the freshly-searched set."""
    db.query(SparkyParlayRanking).filter(
        SparkyParlayRanking.slate_id == slate_id
    ).delete(synchronize_session=False)

    for t in tickets:
        legs = t.legs
        n = len(legs)
        pr = t.pricing
        db.add(SparkyParlayRanking(
            slate_id=slate_id, slate_date=slate_date, rank=t.rank,
            leg1_event_id=legs[0].event_id,
            leg2_event_id=legs[1].event_id if n >= 2 else legs[0].event_id,
            leg3_event_id=legs[2].event_id if n >= 3 else None,
            leg1_pick=legs[0].team_id,
            leg2_pick=legs[1].team_id if n >= 2 else None,
            leg3_pick=legs[2].team_id if n >= 3 else None,
            n_legs=n,
            parlay_odds_american=t.parlay_odds_american,
            parlay_odds_decimal=t.parlay_odds_decimal,
            implied_prob=t.implied_prob,
            combined_win_prob=pr.survive_prob,
            underdog_count=sum(1 for leg in legs if not leg.is_favorite),
            confidence_score=round(pr.survive_prob * 100.0, 1),
            signal_alignment=0.0,
            composite_score=round(t.growth_rate * 1e4, 4),
            expected_value=pr.expected_value,
            kelly_fraction=t.kelly_fraction,
            explanation=t.explanation,
            legs=[leg.as_dict() for leg in legs],
            # New in 0023 — the numbers the ranking actually uses.
            ev_adjusted=t.ev_adjusted,
            selection_penalty=t.selection_penalty,
            growth_rate=t.growth_rate,
            hit_prob=pr.survive_prob,
            correlation_effect=pr.correlation_effect,
            push_effect=pr.push_effect,
            exact_priced=t.exact_priced,
            pricing=pr.as_dict(),
        ))


# --------------------------------------------------------------------------- #
# 4. Settlement grading for multi-market legs
# --------------------------------------------------------------------------- #

WIN, PUSH, LOSS, UNKNOWN = "win", "push", "loss", "unknown"


def grade_leg(leg: dict[str, Any], home_score: float, away_score: float) -> str:
    """Grade one persisted leg against a final score.

    The previous settlement path compared the leg's ``team_id`` against the
    game's winner, which silently graded every spread leg as if it were a
    moneyline and could not represent a total or a push at all. Any slate
    containing one would simply never settle, so accuracy tracking would go
    quiet exactly as the engine started recommending those markets.
    """
    market = leg.get("market") or "moneyline"
    side = leg.get("side")
    line = leg.get("line")
    margin = float(home_score) - float(away_score)

    if market == "moneyline":
        if margin == 0:
            return PUSH  # NFL regular-season ties are real; moneyline pushes
        won_home = margin > 0
        return WIN if (side == "home") == won_home else LOSS

    if market == "spread":
        if line is None:
            return UNKNOWN
        # `line` is stored from the bettor's perspective for the side taken.
        adjusted = margin + float(line) if side == "home" else -margin + float(line)
        if abs(adjusted) < 1e-9:
            return PUSH
        return WIN if adjusted > 0 else LOSS

    if market == "total":
        if line is None:
            return UNKNOWN
        total = float(home_score) + float(away_score)
        diff = total - float(line)
        if abs(diff) < 1e-9:
            return PUSH
        if side == "over":
            return WIN if diff > 0 else LOSS
        return WIN if diff < 0 else LOSS

    return UNKNOWN


def grade_ticket(
    legs: list[dict[str, Any]], scores: dict[str, tuple[float, float]],
) -> tuple[str, float]:
    """Grade a whole ticket. Returns ``(outcome, gross_return_multiple)``.

    Push-aware: a pushed leg drops out and the ticket re-prices at the
    remaining legs, which is why the return multiple has to be recomputed here
    rather than read off the stored parlay price.
    """
    mult = 1.0
    any_live = False
    for leg in legs:
        eid = str(leg.get("event_id") or "")
        if eid not in scores:
            return UNKNOWN, 0.0
        hs, as_ = scores[eid]
        result = grade_leg(leg, hs, as_)
        if result == UNKNOWN:
            return UNKNOWN, 0.0
        if result == LOSS:
            return LOSS, 0.0
        if result == WIN:
            dec = leg.get("decimal_odds")
            if dec is None:
                price = leg.get("price_american")
                if price is None:
                    return UNKNOWN, 0.0
                from .sparky.odds_math import american_to_decimal
                dec = american_to_decimal(int(price))
            mult *= float(dec)
            any_live = True
        # PUSH: leg voids, multiplier unchanged
    if not any_live:
        return PUSH, 1.0
    return WIN, mult
