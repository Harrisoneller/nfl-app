"""Sparky API — NFL betting prediction & parlay intelligence (mounted at /sparky).

Read endpoints serve persisted model output (cheap, no API spend). Admin
endpoints (refresh / backfill) recompute or seed data; they're grouped under
/sparky/admin and intended for the in-app admin/debug view.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..deps import get_db, require_admin
from ..schemas.sparky import (
    AccuracyOut,
    AdminStatusOut,
    GameDetailOut,
    ParlayBoardOut,
    LegMenuOut,
    ParlayOut,
    ParlayRequest,
    PriceTicketRequest,
    SignalGlossaryOut,
    SlateOut,
    ValueBoardOut,
    WeeksOut,
)
from ..services import sparky_parlay_service, sparky_service, sparky_value_service
from ..services.sparky import backtest as sparky_backtest
from ..services.sparky.signals import SIGNAL_DEFINITIONS

router = APIRouter()


def parlay_mod_max_legs() -> int:
    from ..services.sparky import parlay as _p

    return _p.MAX_LEGS


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(400, "date must be ISO format YYYY-MM-DD") from None


@router.get("/weeks", response_model=WeeksOut)
def weeks(season: int | None = None, db: Session = Depends(get_db)):
    """The season's weeks, for the board's week selector.

    Two counts per week and they answer different questions. ``games`` is what
    the schedule says the week contains, so a week with no lines posted yet
    still shows up instead of silently missing. ``priced`` is how many games
    Sparky has actually built a prediction for. 61 scheduled against 0 priced is
    an unbuilt slate, which is a different problem from a quiet week and must
    not look the same.
    """
    return sparky_service.available_weeks(db, season)


@router.get("/slate", response_model=SlateOut)
def slate(
    date: str | None = None,
    prefer_real: bool = False,
    week: int | None = None,
    include_started: bool = False,
    db: Session = Depends(get_db),
):
    """Today's (or a given date's) slate: prediction cards + recommended parlays.

    Set prefer_real=true to exclude synthetic demo data (event_ids starting with 'demo-')
    and prefer the real current/upcoming schedule.
    """
    return sparky_service.get_slate(
        db, _parse_date(date), prefer_real=prefer_real, week=week,
        include_started=include_started,
    )


@router.get("/games/{event_id}", response_model=GameDetailOut)
def game_detail(event_id: str, db: Session = Depends(get_db)):
    """Full detail for one game: prediction, signals, line movement, books."""
    detail = sparky_service.game_detail(db, event_id)
    if detail.get("prediction") is None and not detail.get("books"):
        raise HTTPException(404, f"No Sparky data for event {event_id}")
    return detail


@router.post("/parlay", response_model=ParlayOut)
def parlay(body: ParlayRequest, db: Session = Depends(get_db)):
    """Rank the best tickets buildable from the selected games.

    Searches moneyline, spread and total on each chosen game (not just which
    side wins), prices the combinations with leg correlation and push
    protection, and returns them ranked by expected bankroll growth. -EV
    combinations are labelled rather than hidden, because the user asked about
    these specific games.
    """
    try:
        return sparky_service.rank_parlay(db, body.event_ids, persist=body.persist)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.get("/parlay/legs", response_model=LegMenuOut)
def parlay_legs(event_ids: str, db: Session = Depends(get_db)):
    """Every priceable side of the named games, grouped by game.

    Feeds the builder's leg picker. Each game returns both sides of its
    moneyline, spread and total, with price, book, fair probability, edge and
    EV, so a leg can be chosen on its numbers rather than on a hunch.

    A game with no legs carries a ``reason`` instead — an unpriceable game is a
    fact to report, not a row to omit.
    """
    ids = [e.strip() for e in event_ids.split(",") if e.strip()]
    if not ids:
        raise HTTPException(400, "event_ids must be a comma-separated list")
    if len(ids) > parlay_mod_max_legs():
        raise HTTPException(400, f"At most {parlay_mod_max_legs()} games")
    return sparky_parlay_service.leg_menu(db, ids)


@router.post("/parlay/price", response_model=ParlayOut)
def parlay_price(body: PriceTicketRequest, db: Session = Depends(get_db)):
    """Price one exact ticket the user built leg by leg.

    Unlike ``POST /parlay`` — which takes games and lets the engine choose the
    market and side inside each — this takes the legs themselves and prices
    that one combination. Any mix of games and markets is allowed: a spread in
    one game, a moneyline in another, a total in a third.

    **Same-game legs are supported.** Legs from one game are collapsed into a
    single unit priced on the joint (margin, total) distribution — the only
    correct way to value them — and the result appears in ``same_game``. That
    block leads with a **fair price**, not an EV: books reprice correlated legs
    instead of multiplying them, and this app cannot see that number, so the
    usable output is "worth at least +X, and it can absorb Y% of shading".

    **No selection penalty is charged.** The winner's-curse correction exists
    because searching a slate and reporting the best ticket guarantees the
    reported EV is optimistic. Nobody searched here, so there is no order
    statistic to correct for, and charging it would make a hand-built ticket
    look worse than it is. Per-leg edge shrinkage still applies.
    """
    try:
        return sparky_parlay_service.price_exact_ticket(db, body.leg_keys)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.get("/parlay/board", response_model=ParlayBoardOut)
def parlay_board(
    slate_date: str | None = None,
    legs: str = "2,3,4",
    top_n: int = 6,
    strict: bool = True,
    db: Session = Depends(get_db),
):
    """The slate's best available tickets, found by searching every priceable leg.

    ``strict=true`` (the default) returns only tickets that are still +EV after
    the model's edge is shrunk to what settled history supports and the
    selection penalty is applied. An empty list is a legitimate answer and the
    ``message`` field says so.
    """
    try:
        counts = tuple(
            sorted({int(x) for x in legs.split(",") if x.strip()})
        ) or (2, 3, 4)
    except ValueError:
        raise HTTPException(400, "legs must be a comma-separated list of integers") from None
    parsed = _parse_date(slate_date)
    try:
        return sparky_parlay_service.recommend(
            db, slate_date=parsed, leg_counts=counts,
            top_n=max(1, min(top_n, 25)), strict=strict,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.get("/value-board", response_model=ValueBoardOut)
def value_board(
    slate_date: str | None = None,
    markets: str | None = None,
    include_priced_out: bool = True,
    strictness: str = "balanced",
    week: int | None = None,
    include_started: bool = False,
    db: Session = Depends(get_db),
):
    """Every priceable side on the slate, scored as a standalone bet.

    This is the answer to "what is actually worth betting", as opposed to the
    /slate endpoint's "who is going to win". The two differ most exactly where
    it matters: a 25-point favourite is a near-certain winner and a terrible
    bet, and only one of these endpoints says so.

    Covers moneyline, spread and total in one pass — the leg engine has priced
    all three since the parlay rebuild; only the dashboard was moneyline-only.
    Filter with ``markets=spread,total`` to narrow it.

    Rejected legs are returned in ``priced_out`` with the reason attached rather
    than dropped, because "-2000 needs 95% to break even" is more useful than a
    missing row. ``best_available`` is never empty while anything was priced, so
    the page always shows what came closest even when nothing qualifies.

    ``strictness`` is one of strict | balanced | loose and widens the search
    gates only. Both circuit breakers — the model/market sanity limit and the
    bad-quote limit — are registry-controlled and unaffected by it, because
    widening a search and switching off a safety rail are different acts.

    Check ``diagnostics`` first when the board looks wrong: it reports how many
    games the model actually had a view on, which markets were found, and a
    histogram of why rows were rejected.
    """
    if strictness.lower() not in sparky_value_service.value_mod.STRICTNESS_PRESETS:
        raise HTTPException(
            400,
            "strictness must be one of "
            f"{', '.join(sparky_value_service.value_mod.STRICTNESS_PRESETS)}",
        )
    parsed = _parse_date(slate_date)
    wanted: tuple[str, ...] | None = None
    if markets:
        allowed = set(sparky_value_service.ALL_MARKETS)
        wanted = tuple(
            m for m in (x.strip().lower() for x in markets.split(",")) if m in allowed
        )
        if not wanted:
            raise HTTPException(
                400, "markets must be a comma-separated subset of "
                     f"{','.join(sparky_value_service.ALL_MARKETS)}",
            )
    return sparky_value_service.build_board(
        db, slate_date=parsed, markets=wanted, include_priced_out=include_priced_out,
        strictness=strictness.lower(), week=week, include_started=include_started,
    )


@router.get("/games/{event_id}/value", response_model=ValueBoardOut)
def game_value(event_id: str, db: Session = Depends(get_db)):
    """Every side of one game priced — moneyline, spread and total, both ways."""
    board = sparky_value_service.game_board(db, event_id)
    if not board.get("picks") and not board.get("priced_out"):
        raise HTTPException(404, f"No priced market found for event {event_id}")
    return board


@router.get("/accuracy", response_model=AccuracyOut)
def accuracy(as_of: str | None = None, db: Session = Depends(get_db)):
    """Historical accuracy: rolling windows, by-band, by-signal, parlay rates."""
    return sparky_service.historical_accuracy(db, as_of=_parse_date(as_of))


@router.get("/signals/glossary", response_model=SignalGlossaryOut)
def signal_glossary():
    """The signal taxonomy + definitions (for the UI glossary / admin view)."""
    return {
        "signals": [
            {"key": key, **meta} for key, meta in SIGNAL_DEFINITIONS.items()
        ]
    }


# --- Admin / debug ---------------------------------------------------------- #


@router.get("/admin/status", response_model=AdminStatusOut)
def admin_status(db: Session = Depends(get_db), _: object = Depends(require_admin)):
    """Pipeline health: snapshot counts, last pull, prediction/result counts."""
    return sparky_service.admin_status(db)


@router.post("/admin/refresh", response_model=SlateOut)
async def admin_refresh(
    date: str | None = None,
    week: int | None = None,
    db: Session = Depends(get_db),
    _: object = Depends(require_admin),
):
    """Force-rebuild the slate from the current odds snapshots.

    ``week`` builds a specific NFL week rather than the current one — useful for
    priming next week's board once early lines post.
    """
    return await sparky_service.build_slate(
        db, slate_date=_parse_date(date), week=week,
    )


@router.post("/admin/build_real", response_model=SlateOut)
async def admin_build_real(
    force_refresh: bool = True,
    db: Session = Depends(get_db),
    _: object = Depends(require_admin),
):
    """Clear all synthetic demo data and build a real Sparky slate.

    Step by step:
      1. Wipe every demo row (snapshots + predictions + parlays + results) so
         the synthetic events can't pollute the rebuild — *this is the bit that
         was missing*: without removing demo OddsSnapshots, build_slate would
         just recreate predictions for the same demo events because they still
         pass the upcoming-game time filter.
      2. If we don't already have fresh real snapshots (<2h old), force-pull
         The Odds API (costs ~1 credit). This is what makes the button usable
         in the offseason — the scheduled odds job's offseason guard would
         otherwise leave odds_snapshots empty.
      3. Rebuild the slate from whatever real snapshots we now have.

    The response is the standard SlateOut with an `odds_refresh` envelope so
    the admin UI can surface "Odds API returned N events" or any error.
    """
    from ..models.odds_snapshot import OddsSnapshot
    from ..models.sparky import (
        SparkyGamePrediction,
        SparkyHistoricalResult,
        SparkyParlayRanking,
        SparkyParlayResult,
    )
    from ..services import odds_service

    # 1) Wipe ALL synthetic rows (snapshots are the critical addition vs. before).
    db.query(SparkyGamePrediction).filter(
        SparkyGamePrediction.event_id.like("demo-%")
    ).delete(synchronize_session=False)
    db.query(SparkyHistoricalResult).filter(
        SparkyHistoricalResult.event_id.like("demo-%")
    ).delete(synchronize_session=False)
    db.query(SparkyParlayRanking).filter(
        SparkyParlayRanking.slate_id.like("demo-%")
        | SparkyParlayRanking.leg1_event_id.like("demo-%")
    ).delete(synchronize_session=False)
    db.query(SparkyParlayResult).filter(
        SparkyParlayResult.slate_id.like("demo-%")
    ).delete(synchronize_session=False)
    db.query(OddsSnapshot).filter(
        OddsSnapshot.event_id.like("demo-%")
    ).delete(synchronize_session=False)
    db.commit()

    # 2) Force-pull real odds unless caller opts out, or we already have fresh ones.
    refresh_status: dict | None = None
    if force_refresh:
        fresh_cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
        has_fresh_real = (
            db.query(OddsSnapshot.id)
            .filter(
                ~OddsSnapshot.event_id.like("demo-%"),
                OddsSnapshot.captured_at >= fresh_cutoff,
            )
            .first()
            is not None
        )
        if has_fresh_real:
            refresh_status = {
                "status": "skipped_fresh",
                "message": "Real snapshots <2h old already present",
            }
        else:
            try:
                refresh = await odds_service.refresh_odds(db, force=True)
                refresh_status = {
                    "status": refresh.get("status"),
                    "message": refresh.get("message"),
                    "upstream_events": refresh.get("upstream_events"),
                    "lines_in_db": refresh.get("lines_in_db"),
                }
            except Exception as e:  # noqa: BLE001 — never let this fail the build
                refresh_status = {"status": "error", "message": str(e)[:200]}

    # 3) Build the slate from whatever real snapshots we now have.
    slate = await sparky_service.build_slate(db)
    if refresh_status is not None:
        slate["odds_refresh"] = refresh_status
    return slate


@router.post("/admin/backfill")
async def admin_backfill(
    days: int = 30,
    db: Session = Depends(get_db),
    _: object = Depends(require_admin),
):
    """Seed deterministic synthetic history + a live-looking current slate.

    Use this to populate the dashboard, movement charts, and accuracy views in
    the offseason (or before any live odds have been captured).
    """
    days = max(1, min(120, days))
    result = sparky_service.backfill_demo(db, days=days)
    # Immediately build the seeded current slate so /slate returns data.
    slate = await sparky_service.build_slate(db)
    return {**result, "slate_built": slate.get("count", 0)}


@router.post("/admin/settle")
def admin_settle(
    days: int = 14,
    db: Session = Depends(get_db),
    _: object = Depends(require_admin),
):
    """Run outcome settlement for recent slates whose games have final scores.

    This is the production mechanism that feeds the Historical Accuracy view
    with real results (as opposed to the demo backfill which seeds synthetic
    settled rows). Safe to call repeatedly; settlement is idempotent.
    """
    days = max(1, min(60, days))
    result = sparky_service.settle_sparky_results(db, lookback_days=days)
    return {
        "ok": True,
        "lookback_days": result["lookback_days"],
        "settled_picks": result["settled_picks"],
        "settled_parlays": result["settled_parlays"],
        "skipped": result["skipped"],
    }


@router.post("/admin/backtest")
def admin_backtest(
    start: str,
    end: str,
    mode: str = "replay",
    hours_cutoff: float | None = None,
    db: Session = Depends(get_db),
    _: object = Depends(require_admin),
):
    """
    Run a historical Sparky backtest.

    This is the primary validation tool. Use it to measure whether the current
    engine (signals + confidence + parlays) would have performed well on past
    market data.

    Returns a rich metrics payload (accuracy, Brier, calibration, signal lift,
    simulated ROI, etc.).
    """
    try:
        start_d = date.fromisoformat(start)
        end_d = date.fromisoformat(end)
    except ValueError:
        raise HTTPException(400, "start and end must be YYYY-MM-DD")

    if mode not in ("replay", "settled"):
        raise HTTPException(400, "mode must be 'replay' or 'settled'")

    cfg = sparky_backtest.BacktestConfig(
        start_date=start_d,
        end_date=end_d,
        mode=mode,
        hours_before_kickoff_cutoff=hours_cutoff,
    )
    result = sparky_backtest.run_backtest(db, cfg)

    # Defensive: ensure no NaN values leak into the JSON response
    def safe_num(v):
        if v is None:
            return None
        try:
            if isinstance(v, float) and (v != v or v == float("inf") or v == float("-inf")):
                return None
            return v
        except Exception:
            return None

    return {
        "config": {
            "start": str(cfg.start_date),
            "end": str(cfg.end_date),
            "mode": cfg.mode,
            "hours_cutoff": cfg.hours_before_kickoff_cutoff,
        },
        "summary": result.summary(),
        "metrics": {
            "pick_accuracy": result.pick_accuracy,
            "brier_score": safe_num(result.brier_score),
            "log_loss": safe_num(result.log_loss),
            "calibration": result.calibration,
            "signal_performance": result.signal_performance[:10],
            "parlay": result.parlay_metrics,
            "roi": result.roi_simulation,
        },
        "n_games": result.n_games,
        "n_slates": result.n_slates,
        "generated_at": result.generated_at.isoformat(),
    }
