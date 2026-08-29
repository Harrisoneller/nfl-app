"""The Value Board: every bet on the slate, priced, gated and sized.

What this replaces
------------------
The Sparky dashboard used to be a grid of game cards sorted by
``confidence_score``, and confidence peaks exactly where the model and the
market agree — so the top of the board was permanently occupied by 25-point
favourites tagged "anchor". That is a prediction display wearing a betting
product's clothes. Nobody bets a -2000 moneyline, and nothing about ranking it
first helps find the bets they *would* make.

This module answers a different question for every priceable side of every
market on the slate: **is this worth betting, and how much.**

Three markets, one pipeline
---------------------------
Moneyline, spread and total are not three features. They are three
parameterisations of the same game distribution, and the engine has priced all
three since the parlay rebuild — ``legs.build_leg_pool`` enumerates them, with
exact push mass from the discrete margin PMF. The dashboard simply never asked
for them. The board asks for all three and groups the answer.

Band-aware trust is the substantive change
------------------------------------------
A single global edge-shrinkage lambda applies the same discount to a -2000
favourite and a +150 dog. :func:`shrinkage.fit_edge_lambda_banded` fits one per
probability band instead, so a chalk edge is discounted by what chalk history
supports. Until a band has enough settled rows to speak for itself it falls back
to the global lambda times a deliberately conservative prior — stated as a prior
in the payload, not disguised as a finding, and self-correcting the moment the
history exists.

Nothing is hidden
-----------------
Legs that fail a gate are still priced and still returned, in ``priced_out``,
each carrying the reason it failed. A board that silently drops rows is
indistinguishable from a board that is broken, and the reasons are the most
useful thing on the page: "this needs 95% to break even" teaches more than the
absence of a row.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..logging_config import get_logger
from ..models.sparky import SparkyGamePrediction
from . import param_registry, sparky_parlay_service
from .sparky import legs as leg_mod
from .sparky import odds_math, shrinkage
from .sparky import value as value_mod
from .sparky.legs import GameLegInputs, SidePrices
from .sparky.value import BoardRules, ValuePick

log = get_logger(__name__)

ALL_MARKETS = (leg_mod.MARKET_MONEYLINE, leg_mod.MARKET_SPREAD, leg_mod.MARKET_TOTAL)

# Import-safe fallbacks. Live values resolve per call through `_p` — this
# codebase's standing rule is that params are never read at import time.
_DEFAULTS: dict[str, float] = {
    "value.min_ev": value_mod.DEFAULT_MIN_EV,
    "value.min_p_edge_real": value_mod.DEFAULT_MIN_P_EDGE_REAL,
    "value.ml_price_floor": float(value_mod.DEFAULT_ML_PRICE_FLOOR),
    "value.max_price": float(value_mod.DEFAULT_MAX_PRICE),
    "value.min_books": float(value_mod.DEFAULT_MIN_BOOKS),
    "value.min_stake_units": value_mod.DEFAULT_MIN_STAKE_UNITS,
    "value.kelly_fraction": value_mod.DEFAULT_KELLY_FRACTION,
    "value.kelly_cap": value_mod.DEFAULT_KELLY_CAP,
    "value.tail_tau_inflation": value_mod.DEFAULT_TAIL_TAU_INFLATION,
    "value.max_disagreement": value_mod.DEFAULT_MAX_DISAGREEMENT,
    "value.max_price_edge": value_mod.DEFAULT_MAX_PRICE_EDGE,
    "value.band_prior_chalk": shrinkage.DEFAULT_BAND_PRIORS[shrinkage.BAND_CHALK],
    "value.band_prior_dog": shrinkage.DEFAULT_BAND_PRIORS[shrinkage.BAND_DOG],
}


def _p(key: str) -> float:
    try:
        return float(param_registry.value(key))
    except Exception:  # noqa: BLE001 — must run without a DB or registry
        return _DEFAULTS[key]


DEFAULT_STRICTNESS = "balanced"


def board_rules(strictness: str | None = None) -> BoardRules:
    """Gate bundle for one board render.

    ``strictness`` selects a preset; ``None`` uses the registry values, which is
    what the scheduled/persisted paths want. The presets exist so a reader can
    widen the net themselves when a slate produces nothing, instead of us
    quietly lowering the bar for everyone — but note the two circuit breakers
    (``max_disagreement``, ``max_price_edge``) are read from the registry in
    every case and are deliberately *not* part of any preset. Widening a search
    and disabling a safety rail are different acts and must not share a control.
    """
    base = BoardRules(
        min_ev=_p("value.min_ev"),
        min_p_edge_real=_p("value.min_p_edge_real"),
        ml_price_floor=int(_p("value.ml_price_floor")),
        max_price=int(_p("value.max_price")),
        min_books=int(_p("value.min_books")),
        min_stake_units=_p("value.min_stake_units"),
        kelly_fraction=_p("value.kelly_fraction"),
        kelly_cap=_p("value.kelly_cap"),
        tail_tau_inflation=_p("value.tail_tau_inflation"),
        max_disagreement=_p("value.max_disagreement"),
        max_price_edge=_p("value.max_price_edge"),
    )
    preset = value_mod.STRICTNESS_PRESETS.get((strictness or "").lower())
    if not preset:
        return base
    return BoardRules(
        min_ev=preset["min_ev"],
        min_p_edge_real=preset["min_p_edge_real"],
        ml_price_floor=int(preset["ml_price_floor"]),
        max_price=int(preset["max_price"]),
        min_books=int(preset["min_books"]),
        min_stake_units=preset["min_stake_units"],
        # Sizing and both breakers stay on the registry values.
        kelly_fraction=base.kelly_fraction,
        kelly_cap=base.kelly_cap,
        tail_tau_inflation=base.tail_tau_inflation,
        max_disagreement=base.max_disagreement,
        max_price_edge=base.max_price_edge,
    )


# --------------------------------------------------------------------------- #
# Banded trust, fitted from the same settled history the parlay engine uses
# --------------------------------------------------------------------------- #

_BAND_CACHE: dict[str, Any] = {
    "as_of": None, "banded": None, "dispersion": None, "cal": None,
}


def fit_banded_trust(db: Session, *, force: bool = False) -> dict[str, Any]:
    """Per-band edge shrinkage, dispersion, and the shared calibration map.

    The Platt map is deliberately *not* re-fitted per band and is taken from the
    parlay engine's own fit: calibration is a property of the model's
    probabilities, not of where a bet sits on the price curve, and two boards
    disagreeing about whether a 0.80 means 0.80 would be indefensible.
    """
    rows = sparky_parlay_service.settled_rows(db)
    n = len(rows)
    if not force and _BAND_CACHE["as_of"] == n and _BAND_CACHE["banded"] is not None:
        return {
            "banded": _BAND_CACHE["banded"],
            "dispersion": _BAND_CACHE["dispersion"],
            "cal": _BAND_CACHE["cal"],
        }

    priors = {
        shrinkage.BAND_CHALK: _p("value.band_prior_chalk"),
        shrinkage.BAND_MID: 1.0,
        shrinkage.BAND_DOG: _p("value.band_prior_dog"),
    }
    banded = shrinkage.fit_edge_lambda_banded(rows, priors=priors)
    dispersion = shrinkage.band_dispersion(rows)
    cal = shrinkage.fit_platt(rows)

    _BAND_CACHE.update(
        {"as_of": n, "banded": banded, "dispersion": dispersion, "cal": cal},
    )
    log.info(
        "sparky_value_banded_trust",
        n=n,
        lam_chalk=round(banded.for_band(shrinkage.BAND_CHALK).lam, 4),
        lam_mid=round(banded.for_band(shrinkage.BAND_MID).lam, 4),
        lam_dog=round(banded.for_band(shrinkage.BAND_DOG).lam, 4),
    )
    return {"banded": banded, "dispersion": dispersion, "cal": cal}


def _band_tau(band: str, banded: shrinkage.BandedShrink, dispersion: dict[str, float]) -> float:
    return shrinkage.leg_tau(
        banded.for_band(band),
        dispersion=dispersion.get(band, shrinkage.DEFAULT_EDGE_DISPERSION),
        market_noise=sparky_parlay_service._p("parlay.market_noise"),
    )


# --------------------------------------------------------------------------- #
# Leg pool, built once per band
# --------------------------------------------------------------------------- #


def _holds(games: list[GameLegInputs]) -> dict[tuple[str, str], float]:
    """``(event_id, market) -> book hold`` from each market's consensus pair.

    The hold is not used in any calculation — every probability in this module
    is already de-vigged — but it belongs on the row. It is the single best
    one-glance answer to "why is there no edge in this market", and a board that
    shows a 7% hold next to a 1% claimed edge has explained itself.
    """
    out: dict[tuple[str, str], float] = {}
    for g in games:
        for market, prices in (
            (leg_mod.MARKET_MONEYLINE, g.ml_prices),
            (leg_mod.MARKET_SPREAD, g.spread_prices),
            (leg_mod.MARKET_TOTAL, g.total_prices),
        ):
            if prices is None:
                continue
            if prices.consensus_a is None or prices.consensus_b is None:
                continue
            out[(g.event_id, market)] = odds_math.vig_from_prices(
                prices.consensus_a, prices.consensus_b,
            )
    return out


def _as_utc(value: datetime) -> datetime:
    """Aware UTC — SQLite returns naive datetimes where Postgres does not."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _has_model_view(pred: SparkyGamePrediction) -> bool:
    """Did the model actually produce a distribution for this game?

    This is the single most important flag on the board and it was missing.
    ``build_slate`` writes a prediction row whether or not the model had
    anything to say: when the prediction store is cold on the web role,
    ``_model_pred_map`` returns an empty map, ``market["dist"]`` is written as
    ``{}``, and the ensemble falls back to the de-vigged market probability
    alone.

    Everything downstream then behaves *correctly and uselessly*. The moneyline
    ensemble equals the market, so the edge is identically zero. Spread and
    total legs can still be constructed at a market-fair price so they are
    clickable, but they will not show as value. The board comes out empty of
    recommendations, and every row it does show is labelled "no edge at this
    price" — a claim about the market that we are in no position to make.

    Distinguishing the two states is what lets an empty board say "the model has
    no view on 41 of 44 games, go warm the store" instead of implying the
    market is unbeatable this week.
    """
    return sparky_parlay_service._pred_has_dist(pred)


def _game_inputs_for_slate(
    db: Session,
    slate_date: date | None,
    event_ids: list[str] | None,
    week: int | None = None,
) -> tuple[
    list[GameLegInputs], list[SparkyGamePrediction], dict[str, bool], dict[str, int]
]:
    q = db.query(SparkyGamePrediction)
    if event_ids:
        q = q.filter(SparkyGamePrediction.event_id.in_(event_ids))
    else:
        # When a week is named it is the selector, not the slate_date. A slate
        # can be rebuilt on any date, so filtering on both would return nothing
        # the moment a week was last built on an earlier day.
        if week is not None:
            q = q.filter(SparkyGamePrediction.week == week)
        elif slate_date is not None:
            q = q.filter(SparkyGamePrediction.slate_date == slate_date)
    preds = q.all()
    preds = sparky_parlay_service.fbs_only(preds)
    if not preds:
        return [], [], {}, {}

    sparky_parlay_service.hydrate_model_dist(db, preds)
    odds = sparky_parlay_service._odds_rows(db, [p.event_id for p in preds])
    games: list[GameLegInputs] = []
    model_ok: dict[str, bool] = {}
    for pr in preds:
        model_ok[pr.event_id] = _has_model_view(pr)
        gi = sparky_parlay_service._game_inputs(pr, odds)
        if gi is not None:
            games.append(gi)

    # Raw odds coverage, counted independently of whether a leg was built.
    # A spread leg needs BOTH a book price and a model distribution, so
    # "no spread legs" has two completely different causes with two different
    # fixes. Counting only the legs would have blamed the odds feed for a cold
    # prediction store — which is exactly the wrong thing to go and fix.
    odds_cov = {
        "spread_events": len({e for (e, m) in odds if m == "spreads" and odds[(e, m)]}),
        "total_events": len({e for (e, m) in odds if m == "totals" and odds[(e, m)]}),
    }
    return games, preds, model_ok, odds_cov


def _pool_banded(
    games: list[GameLegInputs],
    *,
    banded: shrinkage.BandedShrink,
    dispersion: dict[str, float],
    cal: shrinkage.PlattMap | None,
    markets: tuple[str, ...],
) -> list[tuple[leg_mod.LegCandidate, str, float]]:
    """Every leg on the slate, each shrunk by *its own band's* trust factor.

    Built one pass per band and filtered to the legs that belong to that band.
    Shrinkage happens inside ``build_leg_pool``, so a leg has to be constructed
    under the lambda that applies to it — there is no way to re-band a leg after
    the fact without re-deriving its probability, which is exactly the kind of
    quiet recomputation that lets two numbers drift apart.

    Every filter here is off, and that is load-bearing rather than lazy: this is
    the *pricing* stage, and the board's contract is that a rejected bet is
    shown with its reason rather than dropped. ``min_books`` in particular must
    stay at 0 — a leg quoted by zero books is exactly the row a user needs to
    see explained, and ``build_leg_pool`` would delete it before
    :mod:`sparky.value` ever got to say why. All gating lives there.
    """
    out: list[tuple[leg_mod.LegCandidate, str, float]] = []
    seen: set[str] = set()
    for band in (shrinkage.BAND_DOG, shrinkage.BAND_MID, shrinkage.BAND_CHALK):
        tau = _band_tau(band, banded, dispersion)
        pool = leg_mod.build_leg_pool(
            games,
            shrink=banded.for_band(band),
            cal=cal,
            tau=tau,
            markets=markets,
            min_edge=-1.0,          # price everything; value.py does the gating
            min_books=0,            # a thin market is a reason to show, not to hide
            max_price=100_000,
            min_price=-100_000,
        )
        for leg in pool:
            if shrinkage.band_for(leg.fair_prob) != band or leg.key in seen:
                continue
            seen.add(leg.key)
            out.append((leg, band, tau))
    return out


# --------------------------------------------------------------------------- #
# The board
# --------------------------------------------------------------------------- #


def build_board(
    db: Session,
    *,
    slate_date: date | None = None,
    event_ids: list[str] | None = None,
    markets: tuple[str, ...] | None = None,
    include_priced_out: bool = True,
    max_priced_out: int = 400,
    strictness: str | None = DEFAULT_STRICTNESS,
    best_available_n: int = 8,
    week: int | None = None,
    include_started: bool = False,
) -> dict[str, Any]:
    """Score every priceable side on the slate and rank what is worth betting.

    Returns four views of the same pool, because "nothing qualifies" and
    "nothing was considered" look identical from the outside and must not:

    - ``picks`` — everything +EV, recommended first, thin edges included.
    - ``best_available`` — the top rows by EV *regardless of tier*, so the page
      is never blank. A slate with no qualifying bet still shows what came
      closest and what stopped it.
    - ``priced_out`` — rejected rows with reasons.
    - ``diagnostics`` — why the board looks the way it does, in aggregate.
    """
    slate_date = slate_date or _latest_slate(db)
    rules = board_rules(strictness)

    games, preds, model_ok, odds_cov = _game_inputs_for_slate(
        db, slate_date, event_ids, week,
    )
    # Started games stay out of the bettable board but are counted, so a board
    # that shrinks through the week says why instead of looking like data loss.
    now = datetime.now(timezone.utc)
    started_ids = {
        p.event_id for p in preds
        if p.commence_time is not None
        and _as_utc(p.commence_time) < now - timedelta(hours=4)
    }
    if not include_started and started_ids:
        preds = [p for p in preds if p.event_id not in started_ids]
        games = [g for g in games if g.event_id not in started_ids]
    if not games:
        return {
            "slate_date": slate_date.isoformat() if slate_date else None,
            "week": week,
            "picks": [], "best_available": [], "priced_out": [],
            "by_market": value_mod.market_summary([]),
            "diagnostics": _diagnostics([], preds, model_ok, [], odds_cov),
            "meta": {"games_considered": 0, "rules": rules.as_dict(),
                     "strictness": strictness},
            "message": (
                "No slate has been built yet, so there is nothing to price. "
                "Build the slate from the current odds board first."
            ),
        }

    trust = fit_banded_trust(db)
    banded: shrinkage.BandedShrink = trust["banded"]
    dispersion: dict[str, float] = trust["dispersion"]

    scored_markets = tuple(markets or ALL_MARKETS)
    holds = _holds(games)
    raw = _pool_banded(
        games, banded=banded, dispersion=dispersion, cal=trust["cal"],
        markets=scored_markets,
    )

    picks: list[ValuePick] = [
        value_mod.score_leg(
            leg, tau=tau, band=band, rules=rules,
            hold=holds.get((leg.event_id, leg.market), 0.0),
            model_available=model_ok.get(leg.event_id, True),
        )
        for leg, band, tau in raw
    ]
    picks.sort(key=lambda p: p.sort_key)

    kickoffs = {p.event_id: p.commence_time for p in preds}
    matchups = {
        p.event_id: {
            "home_team_id": p.home_team_id, "away_team_id": p.away_team_id,
            "home_team": p.home_team, "away_team": p.away_team,
        }
        for p in preds
    }

    def _row(p: ValuePick) -> dict[str, Any]:
        ko = kickoffs.get(p.leg.event_id)
        return {
            **p.as_dict(),
            "explanation": value_mod.explain(p),
            "commence_time": ko.isoformat() if ko else None,
            "matchup": matchups.get(p.leg.event_id, {}),
        }

    live = [p for p in picks if p.tier != value_mod.TIER_PASS]
    passed = [p for p in picks if p.tier == value_mod.TIER_PASS]
    recommended = [p for p in live if p.is_recommended]

    # Never render a blank page. When nothing clears the bar, the most useful
    # thing on the screen is what came closest and what stopped it — ranked by
    # EV rather than by tier, and excluding only the two fault classes, which
    # are not near-misses but broken inputs and would be actively misleading
    # here.
    faults = {value_mod.R_BAD_QUOTE, value_mod.R_MODEL_FAULT, value_mod.R_NO_MODEL}
    best_available = sorted(
        (p for p in picks if p.reason_code not in faults),
        key=lambda p: -p.ev,
    )[:best_available_n]

    diagnostics = _diagnostics(picks, preds, model_ok, games, odds_cov)

    slate_weeks = {p.week for p in preds if p.week is not None}
    body = {
        "slate_date": slate_date.isoformat() if slate_date else None,
        "week": week if week is not None else (
            next(iter(slate_weeks), None) if len(slate_weeks) == 1 else None
        ),
        "picks": [_row(p) for p in live],
        "best_available": [_row(p) for p in best_available],
        "priced_out": [_row(p) for p in _priced_out_slice(passed, max_priced_out)]
        if include_priced_out else [],
        "by_market": value_mod.market_summary(picks),
        "diagnostics": diagnostics,
        "meta": {
            "games_considered": len(games),
            "started_hidden": len(started_ids),
            "include_started": include_started,
            "legs_priced": len(picks),
            "recommended": len(recommended),
            "priced_out_total": len(passed),
            "total_stake_units": round(sum(p.stake_units for p in recommended), 2),
            "markets": list(scored_markets),
            "strictness": strictness,
            "strictness_options": list(value_mod.STRICTNESS_PRESETS),
            "rules": rules.as_dict(),
            "trust": {**banded.as_dict(), "calibration": trust["cal"].as_dict()},
            "band_tau": {
                b: round(_band_tau(b, banded, dispersion), 4)
                for b in (shrinkage.BAND_DOG, shrinkage.BAND_MID, shrinkage.BAND_CHALK)
            },
            "unit_definition": "1 unit = 1% of bankroll",
        },
        "message": None if recommended else _empty_message(diagnostics, rules),
    }

    log.info(
        "sparky_value_board",
        games=len(games), legs=len(picks), recommended=len(recommended),
        no_model=diagnostics["games_without_model"], slate=str(slate_date),
    )
    return body


def _priced_out_slice(passed: list[ValuePick], limit: int) -> list[ValuePick]:
    """Keep consensus (non-alt) legs in the cart even when the cap is tight.

    Alternate numbers can outnumber main-line sides 4-to-1. Truncating the
    rejected list by EV then quietly drops every spread at the number the
    books actually hung, which is exactly the clickable bet the parlay
    builder is for. Consensus first, alts fill whatever room is left.
    """
    if limit <= 0:
        return []
    main = [p for p in passed if not p.leg.is_alt]
    alts = [p for p in passed if p.leg.is_alt]
    return (main + alts)[:limit]


def _diagnostics(
    picks: list[ValuePick],
    preds: list[SparkyGamePrediction],
    model_ok: dict[str, bool],
    games: list[GameLegInputs],
    odds_cov: dict[str, int],
) -> dict[str, Any]:
    """Why the board looks the way it does, in aggregate.

    Reading forty rows to work out that the model had no view on any of them is
    not a reasonable thing to ask of anyone, and it is exactly what an empty
    board demanded before this existed.
    """
    histogram: dict[str, int] = {}
    for p in picks:
        histogram[p.reason_code] = histogram.get(p.reason_code, 0) + 1

    by_market: dict[str, int] = {}
    for p in picks:
        by_market[p.leg.market] = by_market.get(p.leg.market, 0) + 1

    n_games = len(preds)
    without_model = sum(1 for ok in model_ok.values() if not ok)

    spread_markets = sum(1 for g in games if g.spread_prices is not None)
    total_markets = sum(1 for g in games if g.total_prices is not None)
    ml_markets = sum(1 for g in games if g.ml_prices is not None)

    return {
        "games_on_slate": n_games,
        "games_with_model": n_games - without_model,
        "games_without_model": without_model,
        # "priced" = a leg was actually built (needs a book price AND a model
        # distribution). "quoted" = the book prices exist. When quoted > 0 and
        # priced == 0 the odds feed is fine and the model is the problem.
        "markets_found": {
            "moneyline": ml_markets, "spread": spread_markets, "total": total_markets,
        },
        "markets_quoted": {
            "spread": odds_cov.get("spread_events", 0),
            "total": odds_cov.get("total_events", 0),
        },
        "legs_by_market": by_market,
        "rejections": [
            {
                "code": code,
                "label": value_mod.REASON_LABEL.get(code, code),
                "count": n,
            }
            for code, n in sorted(histogram.items(), key=lambda kv: -kv[1])
        ],
        "health": _health_flags(
            n_games, without_model, spread_markets, total_markets,
            odds_cov.get("spread_events", 0), odds_cov.get("total_events", 0),
        ),
    }


def _health_flags(
    n_games: int, without_model: int, spreads: int, totals: int,
    spreads_quoted: int, totals_quoted: int,
) -> list[dict[str, str]]:
    """Actionable warnings about the *data*, not about the market.

    Each one names the fix, because every condition here has a known cause in
    this codebase and a known remedy.
    """
    out: list[dict[str, str]] = []
    if n_games and without_model == n_games:
        out.append({
            "level": "error",
            "title": "The model has no view on any game",
            "detail": (
                "Every prediction row on this slate was written without a model "
                "distribution, which happens when the prediction store is cold and "
                "the web role declines to recompute in-request. With no model view "
                "the moneyline ensemble equals the market (so edge is exactly zero). "
                "Spread and total legs are still built at a market-fair price so "
                "they can be added to a ticket, but they will not show as value "
                "until the store is warm."
            ),
            "fix": "Warm the prediction store on the worker, then rebuild the slate "
                   "(POST /sparky/admin/refresh).",
        })
    elif without_model:
        out.append({
            "level": "warn",
            "title": f"{without_model} of {n_games} games have no model view",
            "detail": "Those games are priced market-only and can never show an edge.",
            "fix": "Rebuild the slate once the prediction store covers the full week.",
        })
    # Only blame the odds feed when the odds are genuinely absent. Spreads
    # can still fail independently of totals: totals match on Over/Under,
    # spreads have to match team names. A moneyline-only board with quoted
    # spreads is almost always a name mismatch, not a missing odds pull.
    if n_games and spreads == 0 and totals == 0:
        if spreads_quoted == 0 and totals_quoted == 0:
            out.append({
                "level": "error",
                "title": "No spread or total prices on the board",
                "detail": (
                    "Spread and total legs are priced from odds_lines, which "
                    "refresh_odds replaces wholesale on each pull. An empty or "
                    "stale odds_lines leaves the board moneyline-only."
                ),
                "fix": "Run an odds refresh, then rebuild the slate.",
            })
        else:
            out.append({
                "level": "warn",
                "title": "Spread and total prices exist but no legs were built",
                "detail": (
                    f"{spreads_quoted} events have spread quotes and {totals_quoted} "
                    "have totals, but none produced a leg. Team names on the odds "
                    "feed did not match the prediction row, so no two-way market "
                    "could be grouped."
                ),
                "fix": "Check odds_lines labels against sparky_game_predictions "
                       "home_team/away_team for one event.",
            })
    elif n_games and spreads == 0 and spreads_quoted > 0:
        out.append({
            "level": "warn",
            "title": "Spread prices exist but no spread legs were built",
            "detail": (
                f"Books quoted a spread on {spreads_quoted} games, but none "
                "could be matched to a home/away side. Totals can still price "
                "because they are Over/Under; spreads cannot."
            ),
            "fix": "Check odds_lines labels against sparky_game_predictions "
                   "home_team/away_team for one event.",
        })
    return out


def _empty_message(diagnostics: dict[str, Any], rules: BoardRules) -> str:
    """What to say when nothing qualified — the true reason, not a platitude."""
    health = diagnostics.get("health") or []
    blocking = next((h for h in health if h.get("level") == "error"), None)
    if blocking:
        return f"{blocking['title']}. {blocking['detail']} Fix: {blocking['fix']}"
    return (
        "Nothing on this slate clears the bar once our edge is shrunk to what "
        "settled history supports. That is a legitimate result — but nothing has "
        "been thrown away: every side we priced is listed below, ranked, with the "
        "reason it fell short. Widen the strictness setting to see more."
    )


def _latest_slate(db: Session) -> date | None:
    from sqlalchemy import func

    latest = db.query(func.max(SparkyGamePrediction.slate_date)).scalar()
    return latest or datetime.now(timezone.utc).date()


# --------------------------------------------------------------------------- #
# Per-game view (used by the game detail page)
# --------------------------------------------------------------------------- #


def game_board(db: Session, event_id: str) -> dict[str, Any]:
    """Every priceable side of one game, scored. Never gated to nothing.

    The slate board answers "what should I bet"; this answers "I am looking at
    this game, what does every number on it price at" — so it returns all six
    sides ranked, passes included.
    """
    return build_board(
        db, event_ids=[event_id], include_priced_out=True, max_priced_out=12,
        strictness="loose", best_available_n=12,
    )
