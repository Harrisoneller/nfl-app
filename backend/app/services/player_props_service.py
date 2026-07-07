"""Player-prop odds: budgeted ingestion, consensus lines, and model edges.

The Odds API only serves player props through its per-event endpoint, and each
(event × market-group) call spends quota — so ingestion is strictly budgeted:
only events kicking off within ``player_props_lookahead_days``, at most
``player_props_max_events`` per run, and never more often than
``player_props_min_refresh_hours``. Snapshots are APPEND-ONLY (the same
contract as ``odds_snapshots``) so prop line movement is preserved.

Edges: for each consensus line we compare the market's de-vigged P(over) with
the projection engine's P(over) from the SAME distribution the product shows
on the player page — one source of truth (see PREDICTION_MODEL_SPEC's
"mutually consistent probabilities" rule). Positive edge = model likes the
Over more than the market does.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..adapters.data.odds_api import TheOddsApiAdapter
from ..cache import cache
from ..config import get_settings
from ..logging_config import get_logger
from ..models.player import Player
from ..models.player_prop_snapshot import PlayerPropSnapshot
from . import player_predictions_service as proj
from .player_projection_engine import anytime_td_prob, stat_over_prob
from .sparky import odds_math

log = get_logger(__name__)

CACHE_TTL = 60 * 15

# Market keys we ingest (all supported by The Odds API for NFL).
DEFAULT_MARKETS: tuple[str, ...] = (
    "player_pass_yds",
    "player_pass_tds",
    "player_rush_yds",
    "player_rush_attempts",
    "player_receptions",
    "player_reception_yds",
    "player_anytime_td",
)

MARKET_LABELS: dict[str, str] = {
    "player_pass_yds": "Passing yards",
    "player_pass_tds": "Passing TDs",
    "player_pass_attempts": "Pass attempts",
    "player_pass_completions": "Completions",
    "player_pass_interceptions": "Interceptions",
    "player_rush_yds": "Rushing yards",
    "player_rush_attempts": "Rush attempts",
    "player_receptions": "Receptions",
    "player_reception_yds": "Receiving yards",
    "player_anytime_td": "Anytime TD",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(s: Any) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


# ---- Ingestion ----------------------------------------------------------------


def _last_refresh(db: Session) -> datetime | None:
    return db.execute(select(func.max(PlayerPropSnapshot.captured_at))).scalar()


async def refresh_player_props(db: Session, *, force: bool = False) -> dict[str, Any]:
    """Pull player-prop odds for near-kickoff events, append-only.

    Budget guards mirror ``odds_service.refresh_odds``: respects a min-interval
    floor, an event-count cap, and a kickoff lookahead window. Returns a status
    dict; never raises for upstream problems.
    """
    settings = get_settings()
    if not settings.player_props_enabled:
        return {"status": "disabled", "rows": 0, "events": 0}
    if not settings.odds_api_key.strip():
        return {"status": "unconfigured", "rows": 0, "events": 0}

    if not force:
        last = _last_refresh(db)
        if last is not None:
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            age_h = (_now() - last).total_seconds() / 3600.0
            if age_h < settings.player_props_min_refresh_hours:
                return {"status": "skipped_fresh", "rows": 0, "events": 0,
                        "message": f"pulled {age_h:.1f}h ago"}

    adapter = TheOddsApiAdapter()
    rows = 0
    events_pulled = 0
    try:
        ev_result = await adapter.fetch_events()
        if ev_result.status != "ok":
            return {"status": ev_result.status, "rows": 0, "events": 0,
                    "message": ev_result.message}

        horizon = _now() + timedelta(days=settings.player_props_lookahead_days)
        upcoming = []
        for ev in ev_result.events:
            commence = _parse_iso(ev.get("commence_time"))
            if commence and _now() - timedelta(hours=6) <= commence <= horizon:
                upcoming.append((commence, ev))
        upcoming.sort(key=lambda t: t[0])
        upcoming = upcoming[: settings.player_props_max_events]

        markets = tuple(
            m.strip() for m in settings.player_props_markets.split(",") if m.strip()
        ) or DEFAULT_MARKETS

        captured_at = _now()
        for commence, ev in upcoming:
            event_id = str(ev.get("id") or "")
            if not event_id:
                continue
            data = await adapter.fetch_event_player_props(event_id, markets)
            if not data:
                continue
            events_pulled += 1
            rows += _store_event_props(db, data, captured_at)
    except Exception as e:  # noqa: BLE001
        log.warning("player_props_refresh_failed", error=str(e)[:200])
        return {"status": "error", "rows": rows, "events": events_pulled, "message": str(e)[:200]}
    finally:
        await adapter.aclose()

    db.commit()
    cache.set("player_props_last_status", {"at": captured_at.isoformat(), "rows": rows}, 86400)
    log.info("player_props_refreshed", rows=rows, events=events_pulled)
    return {"status": "ok", "rows": rows, "events": events_pulled}


def _store_event_props(db: Session, ev: dict[str, Any], captured_at: datetime) -> int:
    """Normalize one event's bookmaker payload into snapshot rows."""
    from .sparky_service import _resolve_id  # shared full-name → team-id map

    event_id = str(ev.get("id") or "")
    commence = _parse_iso(ev.get("commence_time"))
    home_id = _resolve_id(ev.get("home_team"))
    away_id = _resolve_id(ev.get("away_team"))

    # Group by (book, market, player, line): The Odds API lists Over and Under
    # as separate outcomes with `description` = player name.
    grouped: dict[tuple, dict[str, Any]] = {}
    for bm in ev.get("bookmakers", []):
        book = bm.get("title") or bm.get("key") or "unknown"
        for mk in bm.get("markets", []):
            market = mk.get("key") or ""
            for o in mk.get("outcomes", []):
                player_name = (o.get("description") or o.get("name") or "").strip()
                if not player_name:
                    continue
                side = (o.get("name") or "").strip().lower()
                line = o.get("point")
                gkey = (book, market, player_name, line)
                slot = grouped.setdefault(gkey, {})
                price = o.get("price")
                if side in ("over", "yes"):
                    slot["over"] = price
                elif side in ("under", "no"):
                    slot["under"] = price
                else:  # e.g. anytime TD sometimes uses the player name as outcome
                    slot.setdefault("over", price)

    inserted = 0
    for (book, market, player_name, line), prices in grouped.items():
        over_p, under_p = prices.get("over"), prices.get("under")
        if over_p is None and under_p is None:
            continue
        over_imp = under_imp = None
        if over_p is not None and under_p is not None:
            over_imp, under_imp = odds_math.devig_two_way(over_p, under_p)
        elif over_p is not None:
            over_imp = odds_math.american_to_implied(over_p)  # vig-in, one-sided
        db.add(PlayerPropSnapshot(
            event_id=event_id,
            captured_at=captured_at,
            commence_time=commence,
            home_team_id=home_id,
            away_team_id=away_id,
            book=book,
            market=market,
            player_name=player_name,
            player_id=None,  # matched lazily on read (players sync separately)
            line=float(line) if line is not None else None,
            over_price=int(over_p) if over_p is not None else None,
            under_price=int(under_p) if under_p is not None else None,
            over_implied=round(over_imp, 4) if over_imp is not None else None,
            under_implied=round(under_imp, 4) if under_imp is not None else None,
            raw={"event_id": event_id},
        ))
        inserted += 1
    return inserted


# ---- Reading: consensus + model edges ------------------------------------------


def _match_player(db: Session, name: str) -> Player | None:
    key = f"prop_name_match:{name.lower()}"
    if (v := cache.get(key)) is not None:
        return v or None
    p = db.execute(
        select(Player).where(func.lower(Player.full_name) == name.lower())
    ).scalars().first()
    cache.set(key, p or False, 3600)
    return p


def _latest_rows(db: Session, *, player_name: str | None = None) -> list[PlayerPropSnapshot]:
    """Latest capture batch for upcoming events (optionally one player)."""
    q = select(PlayerPropSnapshot).where(
        PlayerPropSnapshot.commence_time >= _now() - timedelta(hours=6)
    )
    if player_name:
        q = q.where(func.lower(PlayerPropSnapshot.player_name) == player_name.lower())
    rows = db.execute(q.order_by(PlayerPropSnapshot.captured_at.desc()).limit(4000)).scalars().all()
    # Keep only the newest row per (event, book, market, player, line).
    seen: set[tuple] = set()
    latest: list[PlayerPropSnapshot] = []
    for r in rows:
        k = (r.event_id, r.book, r.market, r.player_name, r.line)
        if k in seen:
            continue
        seen.add(k)
        latest.append(r)
    return latest


def _consensus(rows: list[PlayerPropSnapshot]) -> list[dict[str, Any]]:
    """Median line + median de-vigged P(over) per (event, market, player)."""
    groups: dict[tuple, list[PlayerPropSnapshot]] = defaultdict(list)
    for r in rows:
        groups[(r.event_id, r.market, r.player_name)].append(r)
    out = []
    for (event_id, market, player_name), rs in groups.items():
        lines = [r.line for r in rs if r.line is not None]
        imps = [r.over_implied for r in rs if r.over_implied is not None]
        out.append({
            "event_id": event_id,
            "market": market,
            "market_label": MARKET_LABELS.get(market, market),
            "player_name": player_name,
            "line": round(median(lines), 1) if lines else None,
            "market_over_prob": round(median(imps), 4) if imps else None,
            "books": len({r.book for r in rs}),
            "commence_time": (
                rs[0].commence_time.isoformat() if rs[0].commence_time else None
            ),
        })
    return out


async def _attach_model(
    db: Session, item: dict[str, Any], player: Player,
) -> dict[str, Any]:
    """Add model P(over) + edge to one consensus row (shared projection cache)."""
    stat = proj.PROP_MARKET_TO_STAT.get(item["market"])
    if stat is None:
        return item
    preds = await proj.player_game_predictions(db, player.id)
    games = preds.get("games") or []
    if not games:
        return item
    nxt = games[0]

    model_over: float | None = None
    if stat == "__anytime_td__":
        lam = sum(
            float(nxt["predicted"][s]["mean"])
            for s in ("rushing_tds", "receiving_tds")
            if s in nxt["predicted"]
        )
        model_over = anytime_td_prob(lam)
    elif item.get("line") is not None and stat in nxt["predicted"]:
        s = nxt["predicted"][stat]
        model_over = stat_over_prob(float(s["mean"]), float(s["sd"]), float(item["line"]))
        item["model_mean"] = s["mean"]
        item["model_sd"] = s["sd"]

    if model_over is not None:
        item["model_over_prob"] = round(model_over, 4)
        if item.get("market_over_prob") is not None:
            item["edge"] = round(model_over - item["market_over_prob"], 4)
            item["side"] = "over" if item["edge"] >= 0 else "under"
    item["player_id"] = player.id
    item["week"] = nxt.get("week")
    item["opponent"] = nxt.get("opponent")
    return item


async def props_for_player(db: Session, player_id: str) -> dict[str, Any]:
    """All current prop markets for one player, with model probs + edges."""
    player = db.get(Player, player_id)
    if player is None:
        return {"player_id": player_id, "error": "player not found", "props": []}
    cache_key = f"player_props:{player_id}"
    if (v := cache.get(cache_key)) is not None:
        return v

    rows = _latest_rows(db, player_name=player.full_name)
    items = _consensus(rows)
    out_items = []
    for item in sorted(items, key=lambda x: x["market"]):
        out_items.append(await _attach_model(db, item, player))

    result = {
        "player_id": player_id,
        "name": player.full_name,
        "count": len(out_items),
        "props": out_items,
        "model_version": proj.MODEL_VERSION,
    }
    cache.set(cache_key, result, CACHE_TTL)
    return result


async def prop_edges(
    db: Session, *, min_edge: float = 0.04, min_books: int = 2, limit: int = 50,
) -> dict[str, Any]:
    """Slate-wide prop edges: every upcoming consensus line where the model and
    the de-vigged market disagree by ≥ min_edge, ranked by |edge|."""
    cache_key = f"prop_edges:{min_edge}:{min_books}:{limit}"
    if (v := cache.get(cache_key)) is not None:
        return v

    rows = _latest_rows(db)
    items = _consensus(rows)
    edged: list[dict[str, Any]] = []
    for item in items:
        if (item.get("books") or 0) < min_books or item.get("market_over_prob") is None:
            continue
        player = _match_player(db, item["player_name"])
        if player is None:
            continue
        item = await _attach_model(db, item, player)
        if item.get("edge") is not None and abs(item["edge"]) >= min_edge:
            edged.append(item)

    edged.sort(key=lambda x: abs(x.get("edge") or 0), reverse=True)
    result = {
        "count": len(edged[:limit]),
        "min_edge": min_edge,
        "min_books": min_books,
        "model_version": proj.MODEL_VERSION,
        "edges": edged[:limit],
        "note": (
            "edge = model P(over) − de-vigged market P(over). Positive → model "
            "likes the Over, negative → the Under. Advisory output only."
        ),
    }
    cache.set(cache_key, result, CACHE_TTL)
    return result


async def prop_board(
    db: Session,
    *,
    market: str | None = None,
    event_id: str | None = None,
    position: str | None = None,
    q: str | None = None,
    limit: int = 250,
) -> dict[str, Any]:
    """The Prop Finder workbench: every upcoming prop with per-book prices,
    best price per side, and the model's probability at each book's exact line.

    Unlike ``prop_edges`` (consensus-only, thresholded), this returns the whole
    board so the UI can filter/sort freely: by market, game, position, player,
    or best available edge. One unfiltered board is built and cached; filters
    are applied per request.
    """
    cache_key = "prop_board_v1"
    board: list[dict[str, Any]] | None = cache.get(cache_key)
    games: dict[str, dict[str, Any]] = cache.get(f"{cache_key}:games") or {}

    if board is None:
        rows = _latest_rows(db)
        groups: dict[tuple, list[PlayerPropSnapshot]] = defaultdict(list)
        for r in rows:
            groups[(r.event_id, r.market, r.player_name)].append(r)

        games = {}
        board = []
        # Model distributions are per (player, stat) — resolve once per player.
        pred_cache: dict[str, dict[str, Any]] = {}

        async def _next_game(player: Player) -> dict[str, Any] | None:
            if player.id not in pred_cache:
                preds = await proj.player_game_predictions(db, player.id)
                g = (preds.get("games") or [None])[0]
                pred_cache[player.id] = g or {}
            return pred_cache[player.id] or None

        for (ev_id, mkt, player_name), rs in groups.items():
            lines = [r.line for r in rs if r.line is not None]
            imps = [r.over_implied for r in rs if r.over_implied is not None]
            consensus_line = round(median(lines), 1) if lines else None
            first = rs[0]
            if ev_id not in games:
                games[ev_id] = {
                    "event_id": ev_id,
                    "home_team_id": first.home_team_id,
                    "away_team_id": first.away_team_id,
                    "commence_time": first.commence_time.isoformat() if first.commence_time else None,
                }

            player = _match_player(db, player_name)
            stat = proj.PROP_MARKET_TO_STAT.get(mkt)
            model_mean = model_sd = None
            anytime_lambda = None
            if player is not None and stat is not None:
                nxt = await _next_game(player)
                if nxt:
                    if stat == "__anytime_td__":
                        anytime_lambda = sum(
                            float(nxt["predicted"][s]["mean"])
                            for s in ("rushing_tds", "receiving_tds")
                            if s in (nxt.get("predicted") or {})
                        )
                    elif stat in (nxt.get("predicted") or {}):
                        s = nxt["predicted"][stat]
                        model_mean, model_sd = float(s["mean"]), float(s["sd"])

            def _model_over(line: float | None) -> float | None:
                if anytime_lambda is not None:
                    return round(anytime_td_prob(anytime_lambda), 4)
                if model_mean is None or model_sd is None or line is None:
                    return None
                return round(stat_over_prob(model_mean, model_sd, float(line)), 4)

            books_out: list[dict[str, Any]] = []
            best_over: dict[str, Any] | None = None
            best_under: dict[str, Any] | None = None
            for r in sorted(rs, key=lambda x: x.book):
                m_over = _model_over(r.line)
                two_sided = r.over_implied is not None and r.under_implied is not None
                edge_over = edge_under = None
                if m_over is not None and two_sided:
                    edge_over = round(m_over - r.over_implied, 4)
                    edge_under = round((1.0 - m_over) - r.under_implied, 4)
                b = {
                    "book": r.book,
                    "line": r.line,
                    "over_price": r.over_price,
                    "under_price": r.under_price,
                    "over_implied": r.over_implied,
                    "under_implied": r.under_implied,
                    "model_over_prob": m_over,
                    "edge_over": edge_over,
                    "edge_under": edge_under,
                }
                books_out.append(b)
                if edge_over is not None and r.over_price is not None:
                    if best_over is None or edge_over > best_over["edge"]:
                        best_over = {"book": r.book, "line": r.line,
                                     "price": r.over_price, "edge": edge_over,
                                     "model_prob": m_over}
                if edge_under is not None and r.under_price is not None:
                    if best_under is None or edge_under > best_under["edge"]:
                        best_under = {"book": r.book, "line": r.line,
                                      "price": r.under_price, "edge": edge_under,
                                      "model_prob": round(1.0 - (m_over or 0.0), 4)}

            best_edge = max(
                [x["edge"] for x in (best_over, best_under) if x], default=None,
            )
            board.append({
                "event_id": ev_id,
                "market": mkt,
                "market_label": MARKET_LABELS.get(mkt, mkt),
                "player_name": player_name,
                "player_id": player.id if player else None,
                "position": (player.position or "").upper() if player else None,
                "team": player.team_id if player else None,
                "commence_time": games[ev_id]["commence_time"],
                "home_team_id": games[ev_id]["home_team_id"],
                "away_team_id": games[ev_id]["away_team_id"],
                "consensus_line": consensus_line,
                "market_over_prob": round(median(imps), 4) if imps else None,
                "books_count": len({r.book for r in rs}),
                "model_mean": round(model_mean, 2) if model_mean is not None else None,
                "model_sd": round(model_sd, 2) if model_sd is not None else None,
                "model_over_prob": _model_over(consensus_line),
                "best_over": best_over,
                "best_under": best_under,
                "best_edge": best_edge,
                "books": books_out,
            })

        board.sort(key=lambda x: (x.get("best_edge") is None, -(x.get("best_edge") or 0)))
        cache.set(cache_key, board, CACHE_TTL)
        cache.set(f"{cache_key}:games", games, CACHE_TTL)

    filtered = board
    if market:
        filtered = [r for r in filtered if r["market"] == market]
    if event_id:
        filtered = [r for r in filtered if r["event_id"] == event_id]
    if position:
        filtered = [r for r in filtered if r.get("position") == position.upper()]
    if q:
        needle = q.strip().lower()
        filtered = [r for r in filtered if needle in r["player_name"].lower()]

    return {
        "count": len(filtered[:limit]),
        "total": len(board),
        "markets": sorted({r["market"] for r in board}),
        "market_labels": MARKET_LABELS,
        "games": sorted(games.values(), key=lambda g: g["commence_time"] or ""),
        "model_version": proj.MODEL_VERSION,
        "note": (
            "edge = model P(side) − book implied P(side) (de-vigged when both "
            "sides are quoted). Advisory output only; not betting advice."
        ),
        "props": filtered[:limit],
    }


def props_status(db: Session) -> dict[str, Any]:
    settings = get_settings()
    last = _last_refresh(db)
    n = db.execute(select(func.count(PlayerPropSnapshot.id))).scalar() or 0
    return {
        "enabled": settings.player_props_enabled,
        "configured": bool(settings.odds_api_key.strip()),
        "markets": settings.player_props_markets,
        "last_refresh": last.isoformat() if last else None,
        "snapshot_rows": int(n),
    }
