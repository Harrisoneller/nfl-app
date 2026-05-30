"""Sparky orchestration — ties the pure engine (app.services.sparky) to the DB
and the app's existing Elo/ML predictor.

Data flow (mirrors SOW 2 §5, adapted to this stack):

    The Odds API pull (existing refresh_odds)
        -> capture_snapshot_from_events()      [append to odds_snapshots]
        -> build_slate()                        [normalize + signals + ensemble]
              -> persist SparkyGamePrediction
              -> persist recommended SparkyParlayRanking
        -> serve via /sparky/* endpoints

Sourcing decision: the *current* line and the *movement history* both come from
``odds_snapshots`` (append-only). That single source unifies the real pipeline
(snapshots captured on each live pull) and the demo path (synthetic snapshots
from :func:`backfill_demo`), so the dashboard looks identical whether the data
is live in-season or backfilled in the offseason. The existing ``odds_lines``
table is left untouched for the existing /odds page.

Ensemble: per Harrison's call, the predicted winner blends the app's Elo/ML home
win probability (``predictions_service.predict_week``) with the de-vigged market
probability, then signals adjust the confidence (see sparky.confidence).
"""
from __future__ import annotations

import random
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..logging_config import get_logger
from ..models.odds import OddsLine
from ..models.odds_snapshot import OddsSnapshot
from ..models.seed import NFL_TEAMS
from ..models.sparky import (
    SparkyGamePrediction,
    SparkyHistoricalResult,
    SparkyParlayRanking,
    SparkyParlayResult,
)
from ..models.game import Game
from ..utils.seasons import current_or_upcoming_season
from ..utils.teams import canonical_team
from . import predictions_service
from .sparky import accuracy as acc
from .sparky import confidence, odds_math, parlay
from .sparky.parlay import GameForParlay
from .sparky.signals import MovementPoint, Signal, SignalInput, detect_signals

log = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Team-name <-> id mapping (The Odds API uses full names; we use 3-letter ids)
# --------------------------------------------------------------------------- #


def _name_to_id() -> dict[str, str]:
    out: dict[str, str] = {}
    for t in NFL_TEAMS:
        full = f"{t['market']} {t['name']}".strip().lower()
        out[full] = t["id"]
        out[t["name"].strip().lower()] = t["id"]
    return out


def _id_to_full() -> dict[str, str]:
    return {t["id"]: f"{t['market']} {t['name']}".strip() for t in NFL_TEAMS}


_NAME_TO_ID = _name_to_id()
_ID_TO_FULL = _id_to_full()


# Quick division lookup for divisional-matchup signal (AFC/NFC East/North/South/West)
def _build_division_map() -> dict[str, tuple[str, str]]:
    return {t["id"]: (t.get("conference", ""), t.get("division", "")) for t in NFL_TEAMS}


_DIVISION_MAP = _build_division_map()


def _is_divisional_game(home_id: str | None, away_id: str | None) -> bool:
    if not home_id or not away_id:
        return False
    hc, hd = _DIVISION_MAP.get(home_id, ("", ""))
    ac, ad = _DIVISION_MAP.get(away_id, ("", ""))
    return bool(hc and hd and hc == ac and hd == ad)


def _resolve_id(full_name: str | None) -> str | None:
    if not full_name:
        return None
    direct = _NAME_TO_ID.get(full_name.strip().lower())
    if direct:
        return direct
    return canonical_team(full_name)  # last resort (handles abbreviations)


# --------------------------------------------------------------------------- #
# Snapshot labeling + capture
# --------------------------------------------------------------------------- #


def _snapshot_label(commence: datetime | None, captured_at: datetime) -> str | None:
    """Bucket a capture into T1..T4 by how far before kickoff it landed."""
    if commence is None:
        return None
    hours = (commence - captured_at).total_seconds() / 3600.0
    if hours <= 0:
        return "T4"          # at/after kickoff (closing)
    if hours <= 6:
        return "T3"          # game day
    if hours <= 72:
        return "T2"          # mid week
    return "T1"              # opener / early week


def _devig_pair(home_ml: int | None, away_ml: int | None) -> tuple[float | None, float | None]:
    if home_ml is None or away_ml is None:
        return None, None
    return odds_math.devig_two_way(home_ml, away_ml)


def capture_snapshot_from_events(
    db: Session, events: list[dict[str, Any]], *, captured_at: datetime | None = None,
) -> int:
    """Append normalized per-book moneyline rows to odds_snapshots.

    Called from ``odds_service.refresh_odds`` with the events it already fetched,
    so this adds ZERO extra Odds API spend. Append-only; we only skip a row if an
    identical line for the same (event, book) was captured very recently, to keep
    forced refreshes from bloating the history.
    """
    now = captured_at or datetime.now(timezone.utc)
    inserted = 0

    for ev in events:
        event_id = str(ev.get("id") or "")
        if not event_id:
            continue
        home = (ev.get("home_team") or "").strip()
        away = (ev.get("away_team") or "").strip()
        commence = _parse_iso(ev.get("commence_time"))
        label = _snapshot_label(commence, now)

        for bm in ev.get("bookmakers", []):
            book = bm.get("title") or bm.get("key") or "unknown"
            home_ml = away_ml = None
            home_spread = away_spread = total = None
            for mk in bm.get("markets", []):
                key = mk.get("key")
                for o in mk.get("outcomes", []):
                    name = (o.get("name") or "").strip()
                    if key == "h2h":
                        if name == home:
                            home_ml = _as_int(o.get("price"))
                        elif name == away:
                            away_ml = _as_int(o.get("price"))
                    elif key == "spreads":
                        if name == home:
                            home_spread = _as_float(o.get("point"))
                        elif name == away:
                            away_spread = _as_float(o.get("point"))
                    elif key == "totals" and name.lower() == "over":
                        total = _as_float(o.get("point"))

            if home_ml is None and away_ml is None and home_spread is None:
                continue  # nothing useful from this book

            h_imp, a_imp = _devig_pair(home_ml, away_ml)
            favorite = None
            if h_imp is not None and a_imp is not None:
                favorite = "home" if h_imp >= a_imp else "away"

            if _is_duplicate_recent(db, event_id, book, home_ml, away_ml, now):
                continue

            db.add(OddsSnapshot(
                event_id=event_id,
                captured_at=now,
                snapshot_label=label,
                commence_time=commence,
                home_team=home or None,
                away_team=away or None,
                home_team_id=_resolve_id(home),
                away_team_id=_resolve_id(away),
                book=book,
                home_ml=home_ml,
                away_ml=away_ml,
                home_spread=home_spread,
                away_spread=away_spread,
                total=total,
                home_implied=h_imp,
                away_implied=a_imp,
                favorite=favorite,
                raw={"event_id": event_id, "book": book},
            ))
            inserted += 1

    if inserted:
        db.commit()
    log.info("sparky_snapshot_captured", rows=inserted, events=len(events))
    return inserted


def _is_duplicate_recent(
    db: Session, event_id: str, book: str, home_ml: int | None, away_ml: int | None, now: datetime,
) -> bool:
    """True if the latest snapshot for (event,book) is identical and < 30m old."""
    latest = (
        db.query(OddsSnapshot)
        .filter(OddsSnapshot.event_id == event_id, OddsSnapshot.book == book)
        .order_by(OddsSnapshot.captured_at.desc())
        .first()
    )
    if latest is None:
        return False
    same = latest.home_ml == home_ml and latest.away_ml == away_ml
    fresh = (now - latest.captured_at).total_seconds() < 30 * 60
    return same and fresh


# --------------------------------------------------------------------------- #
# Reading snapshots: current consensus + movement
# --------------------------------------------------------------------------- #


def _latest_per_book(rows: list[OddsSnapshot]) -> list[OddsSnapshot]:
    """Most-recent snapshot per book for a single event."""
    by_book: dict[str, OddsSnapshot] = {}
    for r in sorted(rows, key=lambda x: x.captured_at):
        by_book[r.book] = r  # later captured_at overwrites
    return list(by_book.values())


def _consensus(rows: list[OddsSnapshot]) -> dict[str, Any]:
    """Consensus line for an event from its latest per-book snapshots."""
    latest = _latest_per_book(rows)
    home_probs = [r.home_implied for r in latest if r.home_implied is not None]
    home_mls = [r.home_ml for r in latest if r.home_ml is not None]
    away_mls = [r.away_ml for r in latest if r.away_ml is not None]
    spreads = [r.home_spread for r in latest if r.home_spread is not None]
    totals = [r.total for r in latest if r.total is not None]

    home_prob = float(statistics.median(home_probs)) if home_probs else 0.5
    return {
        "home_market_prob": home_prob,
        "away_market_prob": 1.0 - home_prob,
        "home_ml": int(statistics.median(home_mls)) if home_mls else None,
        "away_ml": int(statistics.median(away_mls)) if away_mls else None,
        "spread_home": float(statistics.median(spreads)) if spreads else None,
        "total": float(statistics.median(totals)) if totals else None,
        "book_count": len(latest),
        "book_home_probs": home_probs,
        "favorite": "home" if home_prob >= 0.5 else "away",
    }


def _movement(rows: list[OddsSnapshot]) -> list[MovementPoint]:
    """Chronological consensus movement for an event (median home prob per capture)."""
    by_time: dict[datetime, list[OddsSnapshot]] = defaultdict(list)
    for r in rows:
        by_time[r.captured_at].append(r)
    points: list[MovementPoint] = []
    for captured_at in sorted(by_time):
        batch = by_time[captured_at]
        probs = [b.home_implied for b in batch if b.home_implied is not None]
        if not probs:
            continue
        commence = next((b.commence_time for b in batch if b.commence_time), None)
        mins = ((commence - captured_at).total_seconds() / 60.0) if commence else None
        label = _mode_label([b.snapshot_label for b in batch if b.snapshot_label])
        hm = [b.home_ml for b in batch if b.home_ml is not None]
        am = [b.away_ml for b in batch if b.away_ml is not None]
        points.append(MovementPoint(
            label=label or "?",
            minutes_to_kickoff=mins,
            home_prob=float(statistics.median(probs)),
            home_ml=int(statistics.median(hm)) if hm else None,
            away_ml=int(statistics.median(am)) if am else None,
        ))
    return points


def _mode_label(labels: list[str]) -> str | None:
    if not labels:
        return None
    try:
        return statistics.mode(labels)
    except statistics.StatisticsError:
        return labels[-1]


# --------------------------------------------------------------------------- #
# Building the slate
# --------------------------------------------------------------------------- #


async def _model_prob_map(db: Session) -> dict[tuple[str, str], float]:
    """(home_id, away_id) -> Elo/ML ensemble home win prob for the upcoming week."""
    out: dict[tuple[str, str], float] = {}
    try:
        season = current_or_upcoming_season()
        base = await predictions_service.predict_week(db, season, None)
        for g in base.get("games", []):
            h, a = g.get("home_team_id"), g.get("away_team_id")
            pred = g.get("prediction") or {}
            wp = pred.get("home_win_prob")
            if h and a and wp is not None:
                out[(h, a)] = float(wp)
    except Exception as e:  # noqa: BLE001 — model is optional; market-only still works
        log.warning("sparky_model_probs_failed", error=str(e)[:160])
    return out


def _current_event_rows(db: Session) -> dict[str, list[OddsSnapshot]]:
    """All snapshots grouped by event for games that are upcoming/relevant."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=4)
    rows = (
        db.query(OddsSnapshot)
        .filter(
            (OddsSnapshot.commence_time.is_(None))
            | (OddsSnapshot.commence_time >= cutoff)
        )
        .all()
    )
    by_event: dict[str, list[OddsSnapshot]] = defaultdict(list)
    for r in rows:
        by_event[r.event_id].append(r)
    return by_event


def _days_rest_for_team(db: Session, team_id: str, reference: datetime) -> float | None:
    """Return days since the team's most recent *final* game before the reference time.

    Returns None if we have no prior game data. Handles byes naturally (large gaps).
    """
    from ..models.game import Game

    if not team_id or not reference:
        return None

    last = (
        db.query(Game)
        .filter(
            ((Game.home_team_id == team_id) | (Game.away_team_id == team_id))
            & (Game.status.in_(("final", "Final", "STATUS_FINAL", "complete")))
            & (Game.start_time < reference)
        )
        .order_by(Game.start_time.desc())
        .first()
    )
    if last is None or last.start_time is None:
        return None

    delta = reference - last.start_time
    return max(0.0, delta.total_seconds() / 86400.0)  # convert to days (float)


async def build_slate(db: Session, *, slate_date: date | None = None) -> dict[str, Any]:
    """Compute predictions + signals for the current slate and persist them."""
    slate_date = slate_date or datetime.now(timezone.utc).date()
    by_event = _current_event_rows(db)
    model_probs = await _model_prob_map(db)

    games_out: list[dict[str, Any]] = []
    for event_id, rows in by_event.items():
        if not rows:
            continue
        cons = _consensus(rows)
        move = _movement(rows)
        sample = rows[0]
        home_id = sample.home_team_id or _resolve_id(sample.home_team)
        away_id = sample.away_team_id or _resolve_id(sample.away_team)
        model_home = model_probs.get((home_id, away_id)) if home_id and away_id else None

        # NFL context for the new sport-specific signals
        commence = next((r.commence_time for r in rows if r.commence_time), None) or datetime.now(timezone.utc)
        h_rest = _days_rest_for_team(db, home_id, commence) if home_id else None
        a_rest = _days_rest_for_team(db, away_id, commence) if away_id else None
        divisional = _is_divisional_game(home_id, away_id)

        sig_input = SignalInput(
            home_team_id=home_id, away_team_id=away_id,
            favorite=cons["favorite"],
            home_market_prob=cons["home_market_prob"],
            away_market_prob=cons["away_market_prob"],
            home_ml=cons["home_ml"], away_ml=cons["away_ml"],
            spread_home=cons["spread_home"], total=cons["total"],
            book_count=cons["book_count"], book_home_probs=cons["book_home_probs"],
            movement=move, model_home_prob=model_home,
            model_away_prob=(1.0 - model_home) if model_home is not None else None,
            home_rest_days=h_rest,
            away_rest_days=a_rest,
            is_divisional=divisional,
        )
        sigs = detect_signals(sig_input)
        score = confidence.score_game(
            model_home_prob=model_home,
            market_home_prob=cons["home_market_prob"],
            signals=sigs,
        )
        winner_id = home_id if score.predicted_winner_side == "home" else away_id
        loser_id = away_id if score.predicted_winner_side == "home" else home_id
        explanation = confidence.build_explanation(
            winner_id=winner_id or "?", loser_id=loser_id or "?", score=score, signals=sigs,
        )

        market_blob = {
            **cons,
            "home_team_id": home_id,
            "away_team_id": away_id,
            "home_win_prob_ensemble": round(score.home_win_prob, 4),
        }
        commence = next((r.commence_time for r in rows if r.commence_time), None)

        _upsert_prediction(
            db, slate_date=slate_date, event_id=event_id,
            home_id=home_id, away_id=away_id,
            home_name=sample.home_team, away_name=sample.away_team,
            commence=commence, winner_id=winner_id, score=score, signals=sigs,
            explanation=explanation, market=market_blob,
        )
        games_out.append(_prediction_payload(
            event_id, home_id, away_id, sample.home_team, sample.away_team, commence,
            winner_id, score, sigs, explanation, market_blob, move,
        ))

    # Flush the pending prediction upserts so the recommended-parlay step can
    # re-query them in this same session (the session uses autoflush=False).
    db.flush()

    # Auto-build the recommended parlay from the three highest-confidence games.
    games_out.sort(key=lambda g: g["confidence_score"], reverse=True)
    recommended = _persist_recommended_parlays(db, slate_date, games_out)

    db.commit()
    log.info("sparky_slate_built", games=len(games_out), slate=str(slate_date))
    return {
        "slate_date": slate_date.isoformat(),
        "games": games_out,
        "recommended_parlays": recommended,
        "count": len(games_out),
    }


def _upsert_prediction(db: Session, **k) -> None:
    score: confidence.GameScore = k["score"]
    sigs: list[Signal] = k["signals"]
    row = (
        db.query(SparkyGamePrediction)
        .filter(
            SparkyGamePrediction.slate_date == k["slate_date"],
            SparkyGamePrediction.event_id == k["event_id"],
        )
        .first()
    )
    if row is None:
        row = SparkyGamePrediction(slate_date=k["slate_date"], event_id=k["event_id"])
        db.add(row)
    row.home_team_id = k["home_id"]
    row.away_team_id = k["away_id"]
    row.home_team = k["home_name"]
    row.away_team = k["away_name"]
    row.commence_time = k["commence"]
    row.predicted_winner = k["winner_id"]
    row.win_prob = score.win_prob
    row.model_prob = score.model_prob
    row.market_prob = score.market_prob
    row.confidence_score = score.confidence_score
    row.classification = score.classification
    row.signals = [s.as_dict() for s in sigs]
    row.explanation = k["explanation"]
    row.market = k["market"]


def _prediction_payload(event_id, home_id, away_id, home_name, away_name, commence,
                        winner_id, score, sigs, explanation, market, move) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "home_team_id": home_id,
        "away_team_id": away_id,
        "home_team": home_name,
        "away_team": away_name,
        "commence_time": commence.isoformat() if commence else None,
        "predicted_winner": winner_id,
        "win_prob": round(score.win_prob, 4),
        "home_win_prob": round(score.home_win_prob, 4),
        "model_prob": score.model_prob,
        "market_prob": score.market_prob,
        "confidence_score": round(score.confidence_score, 1),
        "base_confidence": round(score.base_confidence, 1),
        "signal_delta": round(score.signal_delta, 1),
        "classification": score.classification,
        "signals": [s.as_dict() for s in sigs],
        "explanation": explanation,
        "market": market,
        "movement": [
            {
                "label": p.label,
                "minutes_to_kickoff": round(p.minutes_to_kickoff, 1) if p.minutes_to_kickoff is not None else None,
                "home_prob": round(p.home_prob, 4),
                "home_ml": p.home_ml,
                "away_ml": p.away_ml,
            }
            for p in move
        ],
    }


# --------------------------------------------------------------------------- #
# Parlay ranking
# --------------------------------------------------------------------------- #


def _game_for_parlay_from_prediction(p: SparkyGamePrediction) -> GameForParlay:
    market = p.market or {}
    sigs = [
        Signal(
            key=s.get("key", ""), label=s.get("label", ""), side=s.get("side", "game"),
            severity=s.get("severity", "info"), magnitude=float(s.get("magnitude", 0.0)),
            weight=float(s.get("weight", 0.0)), explanation=s.get("explanation", ""),
        )
        for s in (p.signals or [])
    ]
    home_prob = market.get("home_win_prob_ensemble")
    if home_prob is None:
        # Reconstruct from predicted winner + win_prob.
        home_prob = p.win_prob if p.predicted_winner == p.home_team_id else 1.0 - p.win_prob
    return GameForParlay(
        event_id=p.event_id,
        home_id=p.home_team_id,
        away_id=p.away_team_id,
        home_ml=market.get("home_ml"),
        away_ml=market.get("away_ml"),
        home_prob=float(home_prob),
        favorite=market.get("favorite", "home"),
        signals=sigs,
        label=f"{p.away_team_id} @ {p.home_team_id}",
    )


def rank_parlay(
    db: Session, event_ids: list[str], *, slate_date: date | None = None, persist: bool = False,
) -> dict[str, Any]:
    """Rank all 8 combinations for three chosen games."""
    if len(event_ids) != 3:
        raise ValueError("Select exactly 3 games for a parlay")
    slate_date = slate_date or datetime.now(timezone.utc).date()
    preds = (
        db.query(SparkyGamePrediction)
        .filter(SparkyGamePrediction.event_id.in_(event_ids))
        .all()
    )
    by_event = {p.event_id: p for p in preds}
    missing = [e for e in event_ids if e not in by_event]
    if missing:
        raise ValueError(f"No prediction found for events: {missing}")

    games = [_game_for_parlay_from_prediction(by_event[e]) for e in event_ids]
    ranked = parlay.generate_parlays(games)
    slate_id = "|".join(sorted(event_ids))

    if persist:
        _persist_parlays(db, slate_id, slate_date, ranked)
        db.commit()

    return {
        "slate_id": slate_id,
        "slate_date": slate_date.isoformat(),
        "games": [
            {
                "event_id": g.event_id, "home_team_id": g.home_id, "away_team_id": g.away_id,
                "home_ml": g.home_ml, "away_ml": g.away_ml, "favorite": g.favorite,
                "home_prob": round(g.home_prob, 4),
            }
            for g in games
        ],
        "parlays": [p.as_dict() for p in ranked],
    }


def _persist_parlays(db: Session, slate_id: str, slate_date: date, ranked: list) -> None:
    db.query(SparkyParlayRanking).filter(SparkyParlayRanking.slate_id == slate_id).delete()
    for p in ranked:
        legs = p.legs
        db.add(SparkyParlayRanking(
            slate_id=slate_id, slate_date=slate_date, rank=p.rank,
            leg1_event_id=legs[0].event_id, leg2_event_id=legs[1].event_id, leg3_event_id=legs[2].event_id,
            leg1_pick=legs[0].team_id, leg2_pick=legs[1].team_id, leg3_pick=legs[2].team_id,
            parlay_odds_american=p.parlay_odds_american, parlay_odds_decimal=p.parlay_odds_decimal,
            implied_prob=p.implied_prob, combined_win_prob=p.combined_win_prob,
            underdog_count=p.underdog_count, confidence_score=p.confidence_score,
            signal_alignment=p.signal_alignment, composite_score=p.composite_score,
            explanation=p.explanation, legs=[leg.as_dict() for leg in legs],
        ))


def _persist_recommended_parlays(
    db: Session, slate_date: date, games_out: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Auto-rank the three most-confident games as the slate's recommended parlay."""
    if len(games_out) < 3:
        return []
    top3 = games_out[:3]
    event_ids = [g["event_id"] for g in top3]
    try:
        result = rank_parlay(db, event_ids, slate_date=slate_date, persist=True)
        return result["parlays"]
    except ValueError:
        return []


# --------------------------------------------------------------------------- #
# Reads: slate, detail, accuracy, admin
# --------------------------------------------------------------------------- #


def get_slate(db: Session, slate_date: date | None = None, *, prefer_real: bool = False) -> dict[str, Any]:
    """Persisted predictions for a slate (most recent if no date given).

    If prefer_real=True, we will ignore purely synthetic demo slates
    (event_ids starting with 'demo-') and only return real data.
    """
    if slate_date is None:
        slate_date = db.query(func.max(SparkyGamePrediction.slate_date)).scalar()

    if slate_date is None:
        has_snapshots = len(_current_event_rows(db)) > 0
        has_current_odds = db.query(OddsLine).count() > 0
        real_data_available = has_snapshots or has_current_odds

        return {
            "slate_date": None,
            "games": [],
            "recommended_parlays": [],
            "count": 0,
            "real_data_available": real_data_available,
        }

    preds_query = db.query(SparkyGamePrediction).filter(
        SparkyGamePrediction.slate_date == slate_date
    )

    if prefer_real:
        # Exclude demo data so real Week 1 schedule shows instead of synthetic
        preds_query = preds_query.filter(
            ~SparkyGamePrediction.event_id.like("demo-%")
        )

    preds = preds_query.all()

    # If prefer_real and we filtered everything out, fall back to most recent real slate
    if prefer_real and not preds:
        real_slate_date = (
            db.query(SparkyGamePrediction.slate_date)
            .filter(~SparkyGamePrediction.event_id.like("demo-%"))
            .order_by(SparkyGamePrediction.slate_date.desc())
            .scalar()
        )
        if real_slate_date:
            preds = (
                db.query(SparkyGamePrediction)
                .filter(SparkyGamePrediction.slate_date == real_slate_date)
                .filter(~SparkyGamePrediction.event_id.like("demo-%"))
                .all()
            )
            slate_date = real_slate_date

    games = [_persisted_prediction_payload(p) for p in preds]
    games.sort(key=lambda g: g["confidence_score"], reverse=True)

    rec_rows = (
        db.query(SparkyParlayRanking)
        .filter(SparkyParlayRanking.slate_date == slate_date)
        .order_by(SparkyParlayRanking.rank.asc())
        .all()
    )
    recommended = [_parlay_row_payload(r) for r in rec_rows]

    return {
        "slate_date": slate_date.isoformat(),
        "games": games,
        "recommended_parlays": recommended,
        "count": len(games),
        "real_data_available": False,
    }

    preds = (
        db.query(SparkyGamePrediction)
        .filter(SparkyGamePrediction.slate_date == slate_date)
        .all()
    )
    games = [_persisted_prediction_payload(p) for p in preds]
    games.sort(key=lambda g: g["confidence_score"], reverse=True)

    rec_rows = (
        db.query(SparkyParlayRanking)
        .filter(SparkyParlayRanking.slate_date == slate_date)
        .order_by(SparkyParlayRanking.rank.asc())
        .all()
    )
    recommended = [_parlay_row_payload(r) for r in rec_rows]
    return {
        "slate_date": slate_date.isoformat(),
        "games": games,
        "recommended_parlays": recommended,
        "count": len(games),
        "real_data_available": False,
    }


def _persisted_prediction_payload(p: SparkyGamePrediction) -> dict[str, Any]:
    return {
        "event_id": p.event_id,
        "home_team_id": p.home_team_id,
        "away_team_id": p.away_team_id,
        "home_team": p.home_team,
        "away_team": p.away_team,
        "commence_time": p.commence_time.isoformat() if p.commence_time else None,
        "predicted_winner": p.predicted_winner,
        "win_prob": round(p.win_prob, 4),
        "model_prob": p.model_prob,
        "market_prob": p.market_prob,
        "confidence_score": round(p.confidence_score, 1),
        "classification": p.classification,
        "signals": p.signals or [],
        "explanation": p.explanation,
        "market": p.market or {},
    }


def _parlay_row_payload(r: SparkyParlayRanking) -> dict[str, Any]:
    return {
        "rank": r.rank,
        "legs": r.legs or [],
        "parlay_odds_american": r.parlay_odds_american,
        "parlay_odds_decimal": round(r.parlay_odds_decimal, 3) if r.parlay_odds_decimal else None,
        "implied_prob": round(r.implied_prob, 4) if r.implied_prob is not None else None,
        "combined_win_prob": round(r.combined_win_prob, 4) if r.combined_win_prob is not None else None,
        "underdog_count": r.underdog_count,
        "confidence_score": round(r.confidence_score, 1),
        "signal_alignment": round(r.signal_alignment, 3),
        "composite_score": round(r.composite_score, 1),
        "explanation": r.explanation,
    }


def game_detail(db: Session, event_id: str) -> dict[str, Any]:
    """Full detail for one game: prediction, full signal list, movement, books."""
    pred = (
        db.query(SparkyGamePrediction)
        .filter(SparkyGamePrediction.event_id == event_id)
        .order_by(SparkyGamePrediction.slate_date.desc())
        .first()
    )
    rows = (
        db.query(OddsSnapshot)
        .filter(OddsSnapshot.event_id == event_id)
        .order_by(OddsSnapshot.captured_at.asc())
        .all()
    )
    movement = [
        {
            "label": p.label,
            "minutes_to_kickoff": round(p.minutes_to_kickoff, 1) if p.minutes_to_kickoff is not None else None,
            "home_prob": round(p.home_prob, 4),
            "home_ml": p.home_ml,
            "away_ml": p.away_ml,
        }
        for p in _movement(rows)
    ]
    latest = _latest_per_book(rows)
    books = [
        {
            "book": r.book, "home_ml": r.home_ml, "away_ml": r.away_ml,
            "home_spread": r.home_spread, "total": r.total,
            "home_implied": round(r.home_implied, 4) if r.home_implied is not None else None,
            "captured_at": r.captured_at.isoformat() if r.captured_at else None,
        }
        for r in latest
    ]
    return {
        "event_id": event_id,
        "prediction": _persisted_prediction_payload(pred) if pred else None,
        "movement": movement,
        "books": books,
        "book_count": len(latest),
    }


def historical_accuracy(db: Session, *, as_of: date | None = None) -> dict[str, Any]:
    """Rolling accuracy windows, by-band, by-signal, parlay rates, and trends."""
    results = db.query(SparkyHistoricalResult).all()
    parlay_results = db.query(SparkyParlayResult).all()

    pick_rows = [
        {
            "date": r.slate_date,
            "correct": r.prediction_correct,
            "confidence": r.confidence_score,
            "classification": r.classification,
            "signals": r.signal_keys or [],
        }
        for r in results
    ]
    parlay_rows = [
        {
            "date": r.slate_date,
            "rank_1_hit": r.rank_1_hit,
            "top_3": r.top_3_containment,
            "top_4": r.top_4_containment,
            "winning_rank": r.winning_combo_rank,
        }
        for r in parlay_results
    ]
    return {
        "sport": "NFL",
        "as_of": (as_of or date.today()).isoformat(),
        "individual_picks": {
            "rolling": acc.rolling_pick_accuracy(pick_rows, as_of),
            "by_confidence_band": acc.accuracy_by_confidence_band(pick_rows),
            "by_signal": acc.accuracy_by_signal(pick_rows),
            "overall": acc.pick_accuracy(pick_rows),
        },
        "parlays": {
            "rolling": acc.rolling_parlay_rates(parlay_rows, as_of),
            "overall": acc.parlay_rates(parlay_rows),
        },
        "trends": acc.performance_trends(pick_rows, parlay_rows),
    }


def admin_status(db: Session) -> dict[str, Any]:
    """Pipeline health for the admin/debug view."""
    snap_count = db.query(func.count(OddsSnapshot.id)).scalar() or 0
    last_snap = db.query(func.max(OddsSnapshot.captured_at)).scalar()
    pred_count = db.query(func.count(SparkyGamePrediction.id)).scalar() or 0
    last_slate = db.query(func.max(SparkyGamePrediction.slate_date)).scalar()
    result_count = db.query(func.count(SparkyHistoricalResult.id)).scalar() or 0
    parlay_count = db.query(func.count(SparkyParlayRanking.id)).scalar() or 0
    distinct_events = db.query(func.count(func.distinct(OddsSnapshot.event_id))).scalar() or 0

    return {
        "snapshots": snap_count,
        "snapshot_events": distinct_events,
        "last_snapshot_at": last_snap.isoformat() if last_snap else None,
        "predictions": pred_count,
        "last_slate_date": last_slate.isoformat() if last_slate else None,
        "settled_results": result_count,
        "parlay_rankings": parlay_count,
        "pipeline_ready": snap_count > 0 and pred_count > 0,
        "has_history_for_movement": distinct_events > 0,
    }


# --------------------------------------------------------------------------- #
# Synthetic backfill (demo) — makes movement signals + accuracy show immediately
# --------------------------------------------------------------------------- #


def backfill_demo(db: Session, *, days: int = 30, seed: int = 1729) -> dict[str, Any]:
    """Generate deterministic synthetic history + a live-looking current slate.

    This exists so the dashboard, movement charts, and accuracy views are fully
    populated even in the offseason (when The Odds API returns no games). It seeds:
      - per-day settled individual picks + parlay results for the trailing window
      - a current/upcoming slate of games with multi-snapshot movement
    All values are produced by the *real* engine, so signal/accuracy distributions
    are representative rather than hand-faked.
    """
    rng = random.Random(seed)
    team_ids = [t["id"] for t in NFL_TEAMS]
    today = datetime.now(timezone.utc).date()

    # Wipe prior demo data so re-running is idempotent.
    db.query(SparkyParlayResult).delete()
    db.query(SparkyHistoricalResult).delete()
    db.commit()

    picks_made = 0
    parlays_made = 0

    for d in range(days, 0, -1):
        slate_day = today - timedelta(days=d)
        n_games = rng.randint(3, 6)
        chosen = rng.sample(team_ids, n_games * 2)
        day_games: list[dict[str, Any]] = []

        for gi in range(n_games):
            home_id = chosen[gi * 2]
            away_id = chosen[gi * 2 + 1]
            event_id = f"demo-{slate_day.isoformat()}-{gi}"

            # A plausible market: favorite prob between 0.5 and 0.85.
            home_fav = rng.random() < 0.55
            fav_prob = rng.uniform(0.52, 0.85)
            home_prob = fav_prob if home_fav else (1.0 - fav_prob)
            home_ml = odds_math.implied_to_american(min(0.95, home_prob * 1.03))
            away_ml = odds_math.implied_to_american(min(0.95, (1 - home_prob) * 1.03))

            # Build a small movement series + per-book dispersion to fire signals.
            move, book_probs = _synthetic_movement(rng, home_prob)
            model_home = min(0.95, max(0.05, home_prob + rng.uniform(-0.10, 0.10)))

            sig_input = SignalInput(
                home_team_id=home_id, away_team_id=away_id,
                favorite="home" if home_prob >= 0.5 else "away",
                home_market_prob=home_prob, away_market_prob=1 - home_prob,
                home_ml=home_ml, away_ml=away_ml,
                spread_home=-rng.uniform(1, 9) if home_fav else rng.uniform(1, 9),
                total=rng.uniform(38, 54), book_count=len(book_probs),
                book_home_probs=book_probs, movement=move,
                model_home_prob=model_home, model_away_prob=1 - model_home,
                # Synthetic but realistic NFL context so the new signals appear in demo
                home_rest_days=rng.choice([4.0, 5.5, 7.0, 7.5, 8.0, 11.0, 13.0]),
                away_rest_days=rng.choice([4.0, 5.5, 7.0, 7.5, 8.0, 11.0, 13.0]),
                is_divisional=(rng.random() < 0.28),  # ~28% of games are divisional in real life
            )
            sigs = detect_signals(sig_input)
            score = confidence.score_game(
                model_home_prob=model_home, market_home_prob=home_prob, signals=sigs,
            )
            winner_side = score.predicted_winner_side
            predicted_winner = home_id if winner_side == "home" else away_id

            # Settle: sample the actual winner from the ensemble home prob.
            actual_home_win = rng.random() < score.home_win_prob
            actual_winner = home_id if actual_home_win else away_id
            correct = predicted_winner == actual_winner

            db.add(SparkyHistoricalResult(
                event_id=event_id, slate_date=slate_day, sport="NFL",
                predicted_winner=predicted_winner, actual_winner=actual_winner,
                prediction_correct=correct, confidence_score=score.confidence_score,
                classification=score.classification,
                signal_keys=[s.key for s in sigs],
                settled_at=datetime.combine(slate_day, datetime.min.time(), tzinfo=timezone.utc),
            ))
            picks_made += 1
            day_games.append({
                "event_id": event_id, "home_id": home_id, "away_id": away_id,
                "home_ml": home_ml, "away_ml": away_ml, "home_prob": score.home_win_prob,
                "favorite": "home" if home_prob >= 0.5 else "away", "signals": sigs,
                "actual_home_win": actual_home_win,
            })

        # Settle a parlay from the day's first three games.
        if len(day_games) >= 3:
            parlays_made += _settle_demo_parlay(db, slate_day, day_games[:3])

    db.commit()

    # Seed a live-looking current slate (upcoming games with movement).
    current = _seed_current_demo_slate(db, rng, team_ids)

    log.info("sparky_backfill_done", days=days, picks=picks_made, parlays=parlays_made,
             current_games=current)
    return {
        "days": days,
        "picks_settled": picks_made,
        "parlays_settled": parlays_made,
        "current_slate_games": current,
    }


def _synthetic_movement(rng: random.Random, home_prob: float) -> tuple[list[MovementPoint], list[float]]:
    """A 3-point movement series + a set of per-book probs around the consensus."""
    drift = rng.uniform(-0.08, 0.08)
    p1 = min(0.95, max(0.05, home_prob - drift))
    p2 = min(0.95, max(0.05, home_prob - drift / 2))
    p3 = home_prob
    move = [
        MovementPoint("T1", 5760, p1),
        MovementPoint("T2", 1440, p2),
        MovementPoint("T3", 120, p3),
    ]
    spread = rng.choice([0.008, 0.012, 0.02, 0.045])  # tight..wide dispersion
    book_probs = [min(0.97, max(0.03, home_prob + rng.uniform(-spread, spread))) for _ in range(6)]
    return move, book_probs


def _settle_demo_parlay(db: Session, slate_day: date, games: list[dict[str, Any]]) -> int:
    parlay_games = [
        GameForParlay(
            event_id=g["event_id"], home_id=g["home_id"], away_id=g["away_id"],
            home_ml=g["home_ml"], away_ml=g["away_ml"], home_prob=g["home_prob"],
            favorite=g["favorite"], signals=g["signals"],
        )
        for g in games
    ]
    ranked = parlay.generate_parlays(parlay_games)
    actual_winner_side = {g["event_id"]: ("home" if g["actual_home_win"] else "away") for g in games}

    winning_rank = None
    for p in ranked:
        if all(leg.side == actual_winner_side[leg.event_id] for leg in p.legs):
            winning_rank = p.rank
            break

    slate_id = "|".join(sorted(g["event_id"] for g in games))
    db.add(SparkyParlayResult(
        slate_id=slate_id, slate_date=slate_day, sport="NFL", n_parlays=len(ranked),
        winning_combo_rank=winning_rank,
        rank_1_hit=(winning_rank == 1),
        top_3_containment=(winning_rank is not None and winning_rank <= 3),
        top_4_containment=(winning_rank is not None and winning_rank <= 4),
        settled_at=datetime.combine(slate_day, datetime.min.time(), tzinfo=timezone.utc),
    ))
    return 1


def _seed_current_demo_slate(db: Session, rng: random.Random, team_ids: list[str]) -> int:
    """Insert upcoming-game snapshots so build_slate has live-looking input."""
    now = datetime.now(timezone.utc)
    # Clear any prior demo snapshots (event ids prefixed 'demo-live-').
    db.query(OddsSnapshot).filter(OddsSnapshot.event_id.like("demo-live-%")).delete(
        synchronize_session=False
    )
    n_games = 6
    chosen = rng.sample(team_ids, n_games * 2)
    books = ["DraftKings", "FanDuel", "BetMGM", "Caesars", "PointsBet", "Bovada"]
    for gi in range(n_games):
        home_id, away_id = chosen[gi * 2], chosen[gi * 2 + 1]
        event_id = f"demo-live-{gi}"
        commence = now + timedelta(days=rng.randint(1, 6), hours=rng.randint(0, 12))
        home_fav = rng.random() < 0.55
        fav_prob = rng.uniform(0.52, 0.86)
        home_prob = fav_prob if home_fav else 1 - fav_prob
        move, _ = _synthetic_movement(rng, home_prob)
        disp = rng.choice([0.008, 0.012, 0.02, 0.05])

        # Three capture times mirroring the movement series.
        for mp, hrs_before in zip(move, (96, 24, 2)):
            captured_at = commence - timedelta(hours=hrs_before)
            for book in books:
                hp = min(0.97, max(0.03, mp.home_prob + rng.uniform(-disp, disp)))
                home_ml = odds_math.implied_to_american(min(0.96, hp * 1.03))
                away_ml = odds_math.implied_to_american(min(0.96, (1 - hp) * 1.03))
                h_imp, a_imp = odds_math.devig_two_way(home_ml, away_ml)
                db.add(OddsSnapshot(
                    event_id=event_id, captured_at=captured_at,
                    snapshot_label=_snapshot_label(commence, captured_at),
                    commence_time=commence,
                    home_team=_ID_TO_FULL.get(home_id), away_team=_ID_TO_FULL.get(away_id),
                    home_team_id=home_id, away_team_id=away_id, book=book,
                    home_ml=home_ml, away_ml=away_ml,
                    home_spread=-rng.uniform(1, 9) if home_fav else rng.uniform(1, 9),
                    away_spread=rng.uniform(1, 9) if home_fav else -rng.uniform(1, 9),
                    total=round(rng.uniform(38, 54), 1),
                    home_implied=h_imp, away_implied=a_imp,
                    favorite="home" if hp >= 0.5 else "away",
                    raw={"demo": True},
                ))
    db.commit()
    return n_games


# --------------------------------------------------------------------------- #
# Small parsing helpers
# --------------------------------------------------------------------------- #


def _parse_iso(s: Any) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _as_int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (ValueError, TypeError):
        return None


def _as_float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- #
# Real outcome settlement (makes accuracy reporting reflect live results)
# --------------------------------------------------------------------------- #


def _actual_winner_from_game(g: Game) -> str | None:
    """Return the winning team_id (home or away) for a final game, or None."""
    if g.home_score is None or g.away_score is None:
        return None
    if g.home_score > g.away_score:
        return g.home_team_id
    if g.away_score > g.home_score:
        return g.away_team_id
    return None  # tie (very rare)


def settle_sparky_results(
    db: Session, *, lookback_days: int = 14
) -> dict[str, Any]:
    """Idempotent settlement of completed games into Sparky historical accuracy tables.

    For every recent SparkyGamePrediction whose corresponding Game row is "final"
    with scores, we record the actual winner + correctness into SparkyHistoricalResult.

    For every persisted 3-leg parlay slate (SparkyParlayRanking) where all three
    legs have now settled, we determine which of the 8 ranked combinations actually
    hit and record rank_1_hit / top-3 containment etc into SparkyParlayResult.

    This is the production path that turns the accuracy dashboard from "demo only"
    into a live, trustworthy performance report (the backfill_demo path is for
    offseason exploration).
    """
    cutoff = date.today() - timedelta(days=lookback_days)

    preds = (
        db.query(SparkyGamePrediction)
        .filter(SparkyGamePrediction.slate_date >= cutoff)
        .all()
    )

    settled_picks = 0
    settled_parlays = 0
    skipped = 0
    event_to_actual: dict[str, str] = {}

    # --- Individual pick settlement ---
    for p in preds:
        if not p.predicted_winner or not p.home_team_id or not p.away_team_id:
            skipped += 1
            continue

        # Direct match by event_id (The Odds API + ESPN ids align in this stack)
        game = db.get(Game, p.event_id)

        # Fallback: fuzzy match by teams + time window (handles any id skew)
        if game is None and p.commence_time is not None:
            game = (
                db.query(Game)
                .filter(
                    ((Game.home_team_id == p.home_team_id) & (Game.away_team_id == p.away_team_id))
                    | ((Game.home_team_id == p.away_team_id) & (Game.away_team_id == p.home_team_id))
                )
                .filter(Game.start_time.between(
                    p.commence_time - timedelta(hours=4),
                    p.commence_time + timedelta(hours=4),
                ))
                .first()
            )

        if game is None:
            skipped += 1
            continue

        status = (game.status or "").lower()
        if status not in ("final", "status_final", "complete", "finished"):
            skipped += 1
            continue

        actual = _actual_winner_from_game(game)
        if actual is None:
            skipped += 1
            continue

        event_to_actual[p.event_id] = actual
        correct = p.predicted_winner == actual

        # Upsert (unique constraint on event_id)
        row = (
            db.query(SparkyHistoricalResult)
            .filter(SparkyHistoricalResult.event_id == p.event_id)
            .first()
        )
        if row is None:
            row = SparkyHistoricalResult(
                event_id=p.event_id,
                slate_date=p.slate_date,
                sport="NFL",
            )
            db.add(row)

        row.predicted_winner = p.predicted_winner
        row.actual_winner = actual
        row.prediction_correct = correct
        row.confidence_score = p.confidence_score
        row.classification = p.classification
        row.signal_keys = [s.get("key") for s in (p.signals or []) if isinstance(s, dict)]
        row.settled_at = datetime.now(timezone.utc)
        row.game_id = game.id
        settled_picks += 1

    db.flush()

    # --- Parlay slate settlement (only for slates whose 3 legs are all now known) ---
    recent_rankings = (
        db.query(SparkyParlayRanking)
        .filter(SparkyParlayRanking.slate_date >= cutoff)
        .all()
    )
    by_slate: dict[str, list[SparkyParlayRanking]] = defaultdict(list)
    for r in recent_rankings:
        by_slate[r.slate_id].append(r)

    for slate_id, rows in by_slate.items():
        if not rows:
            continue
        # Use the legs JSON from the first row (all rows for a slate share the same 3 events)
        sample_legs = rows[0].legs or []
        leg_ids = [str(leg.get("event_id")) for leg in sample_legs if leg.get("event_id")]
        if len(leg_ids) != 3:
            continue
        if any(eid not in event_to_actual for eid in leg_ids):
            continue  # not fully settled yet

        # Find which ranked combination actually hit
        winning_rank: int | None = None
        for r in sorted(rows, key=lambda x: x.rank or 99):
            hit = True
            for leg in (r.legs or []):
                eid = str(leg.get("event_id") or "")
                chosen = leg.get("team_id")
                if event_to_actual.get(eid) != chosen:
                    hit = False
                    break
            if hit:
                winning_rank = r.rank
                break

        # Upsert parlay result row
        pr = (
            db.query(SparkyParlayResult)
            .filter(SparkyParlayResult.slate_id == slate_id)
            .first()
        )
        if pr is None:
            pr = SparkyParlayResult(
                slate_id=slate_id,
                slate_date=rows[0].slate_date,
                sport="NFL",
            )
            db.add(pr)

        pr.n_parlays = len(rows)
        pr.winning_combo_rank = winning_rank
        pr.rank_1_hit = (winning_rank == 1) if winning_rank is not None else False
        pr.top_3_containment = (winning_rank is not None and winning_rank <= 3)
        pr.top_4_containment = (winning_rank is not None and winning_rank <= 4)
        pr.settled_at = datetime.now(timezone.utc)
        settled_parlays += 1

    db.commit()

    log.info(
        "sparky_settlement_complete",
        picks=settled_picks,
        parlays=settled_parlays,
        skipped=skipped,
        lookback=lookback_days,
    )
    return {
        "settled_picks": settled_picks,
        "settled_parlays": settled_parlays,
        "skipped": skipped,
        "lookback_days": lookback_days,
    }
