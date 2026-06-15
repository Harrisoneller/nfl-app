"""Bet tracker service — create, list, settle, and profile aggregation.

Settlement is idempotent: it only touches pending legs/bets, grades any whose
game has gone final, computes CLV against the odds_snapshots closing capture,
and rolls leg results up into the bet outcome. Safe to call repeatedly (e.g. on
a schedule or when the user opens their profile).
"""
from __future__ import annotations

import statistics
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.bet import LOST, PENDING, PUSH, WON, Bet, BetLeg
from ..models.game import Game
from ..models.odds_snapshot import OddsSnapshot
from .bets import grading
from .sparky.odds_math import american_to_decimal

_FINAL = ("final", "status_final", "complete", "finished", "post")


# --------------------------------------------------------------------------- #
# Create / read / delete
# --------------------------------------------------------------------------- #


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_bet(db: Session, user_id: uuid.UUID, payload: Any) -> Bet:
    """Create a bet from a validated BetCreate payload."""
    legs: list[BetLeg] = []
    leg_decimals: list[float] = []
    for lc in payload.legs:
        dec = american_to_decimal(lc.odds_american)
        leg_decimals.append(dec)
        legs.append(
            BetLeg(
                event_id=lc.event_id,
                game_id=lc.game_id,
                market=lc.market,
                selection=lc.selection,
                selection_label=lc.selection_label or _default_label(lc),
                line=lc.line,
                odds_american=lc.odds_american,
                odds_decimal=dec,
                home_team_id=lc.home_team_id,
                away_team_id=lc.away_team_id,
                commence_time=lc.commence_time,
                leg_result=PENDING,
            )
        )

    combined_decimal = 1.0
    for d in leg_decimals:
        combined_decimal *= d
    combined_american = (
        legs[0].odds_american if len(legs) == 1 else _dec_to_american(combined_decimal)
    )

    bet = Bet(
        user_id=user_id,
        bet_type=payload.bet_type,
        status=PENDING,
        source=payload.source,
        note=payload.note or "",
        stake_units=payload.stake_units,
        stake_dollars=payload.stake_dollars,
        odds_american=combined_american,
        odds_decimal=combined_decimal,
        placed_at=payload.placed_at or _now(),
        legs=legs,
    )
    db.add(bet)
    db.commit()
    db.refresh(bet)
    return bet


def _dec_to_american(decimal_odds: float) -> int:
    from .sparky.odds_math import decimal_to_american

    try:
        return decimal_to_american(decimal_odds)
    except ValueError:
        return 100


def _default_label(lc: Any) -> str:
    if lc.market == "moneyline":
        return f"{lc.selection} ML"
    if lc.market == "spread":
        sign = "+" if (lc.line or 0) > 0 else ""
        return f"{lc.selection} {sign}{lc.line}"
    # total
    return f"{lc.selection.capitalize()} {lc.line}"


def list_bets(db: Session, user_id: uuid.UUID, status: str | None = None) -> list[Bet]:
    stmt = select(Bet).where(Bet.user_id == user_id)
    if status:
        stmt = stmt.where(Bet.status == status)
    stmt = stmt.order_by(Bet.placed_at.desc())
    return list(db.execute(stmt).scalars().all())


def get_bet(db: Session, user_id: uuid.UUID, bet_id: uuid.UUID) -> Bet | None:
    bet = db.get(Bet, bet_id)
    if bet is None or bet.user_id != user_id:
        return None
    return bet


def delete_bet(db: Session, user_id: uuid.UUID, bet_id: uuid.UUID) -> bool:
    bet = get_bet(db, user_id, bet_id)
    if bet is None:
        return False
    db.delete(bet)
    db.commit()
    return True


# --------------------------------------------------------------------------- #
# Settlement
# --------------------------------------------------------------------------- #


def _find_game(db: Session, leg: BetLeg) -> Game | None:
    """Resolve the Game row for a leg (direct event_id, then team+time fuzzy)."""
    if leg.event_id:
        g = db.get(Game, leg.event_id)
        if g is not None:
            return g
    if leg.game_id:
        g = db.get(Game, leg.game_id)
        if g is not None:
            return g
    if leg.home_team_id and leg.away_team_id and leg.commence_time:
        return (
            db.query(Game)
            .filter(
                ((Game.home_team_id == leg.home_team_id) & (Game.away_team_id == leg.away_team_id))
                | ((Game.home_team_id == leg.away_team_id) & (Game.away_team_id == leg.home_team_id))
            )
            .filter(
                Game.start_time.between(
                    leg.commence_time - timedelta(hours=6),
                    leg.commence_time + timedelta(hours=6),
                )
            )
            .first()
        )
    return None


def _closing_snapshot(db: Session, leg: BetLeg) -> list[OddsSnapshot]:
    """Closing-capture rows for the leg's event (T4 if present, else latest)."""
    if not leg.event_id:
        return []
    rows = (
        db.query(OddsSnapshot)
        .filter(OddsSnapshot.event_id == leg.event_id)
        .filter(OddsSnapshot.snapshot_label == "T4")
        .all()
    )
    if rows:
        return rows
    # Fallback: the latest capture time we have for this event.
    latest = (
        db.query(OddsSnapshot)
        .filter(OddsSnapshot.event_id == leg.event_id)
        .order_by(OddsSnapshot.captured_at.desc())
        .first()
    )
    if latest is None:
        return []
    return (
        db.query(OddsSnapshot)
        .filter(OddsSnapshot.event_id == leg.event_id)
        .filter(OddsSnapshot.captured_at == latest.captured_at)
        .all()
    )


def _median(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    return statistics.median(vals) if vals else None


def _apply_clv(db: Session, leg: BetLeg) -> None:
    """Compute + store CLV for a leg against the consensus closing number."""
    snaps = _closing_snapshot(db, leg)
    if not snaps:
        return
    is_home = leg.selection == leg.home_team_id

    if leg.market == "moneyline":
        prices = [s.home_ml if is_home else s.away_ml for s in snaps]
        close = _median([float(p) for p in prices if p is not None])
        if close is None:
            return
        close_american = int(round(close))
        leg.closing_odds_american = close_american
        leg.clv_pct = round(grading.clv_moneyline(leg.odds_american, close_american), 2)
        leg.beat_close = leg.clv_pct > 0
    elif leg.market == "spread":
        # Snapshot stores home_spread/away_spread; pick the selection's side.
        nums = [s.home_spread if is_home else s.away_spread for s in snaps]
        close = _median([float(n) for n in nums if n is not None])
        if close is None or leg.line is None:
            return
        leg.closing_line = round(close, 1)
        leg.clv_line = round(grading.clv_spread(leg.line, close), 1)
        leg.beat_close = leg.clv_line > 0
    elif leg.market == "total":
        nums = [s.total for s in snaps]
        close = _median([float(n) for n in nums if n is not None])
        if close is None or leg.line is None:
            return
        leg.closing_line = round(close, 1)
        leg.clv_line = round(grading.clv_total(leg.selection, leg.line, close), 1)
        leg.beat_close = leg.clv_line > 0


def _grade_leg(db: Session, leg: BetLeg) -> str | None:
    """Grade a single leg if its game is final; returns the result or None."""
    game = _find_game(db, leg)
    if game is None:
        return None
    if (game.status or "").lower() not in _FINAL:
        return None
    if game.home_score is None or game.away_score is None:
        return None
    if not game.home_team_id or not game.away_team_id:
        return None

    if leg.market == "moneyline":
        return grading.grade_moneyline(
            leg.selection, game.home_team_id, game.away_team_id, game.home_score, game.away_score
        )
    if leg.market == "spread":
        if leg.line is None:
            return None
        return grading.grade_spread(
            leg.line, leg.selection, game.home_team_id, game.away_team_id,
            game.home_score, game.away_score,
        )
    if leg.market == "total":
        if leg.line is None:
            return None
        return grading.grade_total(leg.selection, leg.line, game.home_score, game.away_score)
    return None


def settle_user_bets(db: Session, user_id: uuid.UUID) -> dict[str, int]:
    """Idempotently settle all of a user's pending bets. Returns counts."""
    pending = list_bets(db, user_id, status=PENDING)
    settled_bets = 0
    graded_legs = 0

    for bet in pending:
        changed = False
        for leg in bet.legs:
            if leg.leg_result != PENDING:
                continue
            # CLV can be computed as soon as a closing snapshot exists, even
            # before the game is final.
            if leg.clv_pct is None and leg.clv_line is None:
                _apply_clv(db, leg)
            result = _grade_leg(db, leg)
            if result is not None:
                leg.leg_result = result
                leg.settled_at = _now()
                graded_legs += 1
                changed = True

        # Roll up the bet if every leg is now graded.
        leg_results = [leg.leg_result for leg in bet.legs]
        if leg_results and all(r != PENDING for r in leg_results):
            leg_decimals = [leg.odds_decimal for leg in bet.legs]
            if bet.bet_type == "parlay":
                status, payout, result = grading.settle_parlay(
                    leg_results, leg_decimals, bet.stake_units
                )
            else:
                status, payout, result = grading.settle_straight(
                    leg_results[0], leg_decimals[0], bet.stake_units
                )
            bet.status = status
            bet.payout_units = round(payout, 4)
            bet.result_units = round(result, 4)
            if bet.stake_dollars:
                ratio = result / bet.stake_units if bet.stake_units else 0.0
                bet.result_dollars = round(ratio * bet.stake_dollars, 2)
            bet.settled_at = _now()
            bet.clv_pct, bet.beat_close = _rollup_clv(bet)
            settled_bets += 1
            changed = True
        elif changed:
            # Legs updated (e.g. CLV filled) but bet still pending.
            bet.clv_pct, bet.beat_close = _rollup_clv(bet)

        if changed:
            db.add(bet)

    db.commit()
    return {"settled_bets": settled_bets, "graded_legs": graded_legs, "pending_scanned": len(pending)}


def _rollup_clv(bet: Bet) -> tuple[float | None, bool | None]:
    ml_clvs = [leg.clv_pct for leg in bet.legs if leg.clv_pct is not None]
    beats = [leg.beat_close for leg in bet.legs if leg.beat_close is not None]
    avg = round(sum(ml_clvs) / len(ml_clvs), 2) if ml_clvs else None
    beat = (sum(1 for b in beats if b) >= (len(beats) / 2)) if beats else None
    return avg, beat


# --------------------------------------------------------------------------- #
# Profile aggregation
# --------------------------------------------------------------------------- #


def profile_summary(db: Session, user_id: uuid.UUID) -> dict[str, Any]:
    bets = list_bets(db, user_id)
    settled = [b for b in bets if b.status in (WON, LOST, PUSH)]
    pending = [b for b in bets if b.status == PENDING]

    won = sum(1 for b in settled if b.status == WON)
    lost = sum(1 for b in settled if b.status == LOST)
    push = sum(1 for b in settled if b.status == PUSH)

    staked_units = sum(b.stake_units for b in settled)
    profit_units = sum((b.result_units or 0.0) for b in settled)
    open_risk = sum(b.stake_units for b in pending)

    dollar_settled = [b for b in settled if b.stake_dollars]
    staked_dollars = sum(b.stake_dollars for b in dollar_settled) or None
    profit_dollars = (
        sum((b.result_dollars or 0.0) for b in dollar_settled) if dollar_settled else None
    )

    # CLV across all settled legs.
    all_legs = [leg for b in settled for leg in b.legs]
    ml_clvs = [leg.clv_pct for leg in all_legs if leg.clv_pct is not None]
    beats = [leg.beat_close for leg in all_legs if leg.beat_close is not None]

    by_market: dict[str, dict[str, int]] = {}
    for leg in all_legs:
        rec = by_market.setdefault(leg.market, {"won": 0, "lost": 0, "push": 0})
        if leg.leg_result in rec:
            rec[leg.leg_result] += 1

    by_type: dict[str, dict[str, int]] = {}
    for b in settled:
        rec = by_type.setdefault(b.bet_type, {"won": 0, "lost": 0, "push": 0})
        if b.status in rec:
            rec[b.status] += 1

    return {
        "total_bets": len(bets),
        "pending": len(pending),
        "settled": len(settled),
        "won": won,
        "lost": lost,
        "push": push,
        "win_rate": round(won / (won + lost), 4) if (won + lost) else None,
        "staked_units": round(staked_units, 2),
        "profit_units": round(profit_units, 2),
        "roi_pct": round(100 * profit_units / staked_units, 2) if staked_units else None,
        "open_risk_units": round(open_risk, 2),
        "staked_dollars": round(staked_dollars, 2) if staked_dollars else None,
        "profit_dollars": round(profit_dollars, 2) if profit_dollars is not None else None,
        "roi_dollars_pct": (
            round(100 * profit_dollars / staked_dollars, 2)
            if staked_dollars and profit_dollars is not None
            else None
        ),
        "avg_clv_pct": round(sum(ml_clvs) / len(ml_clvs), 2) if ml_clvs else None,
        "beat_close_pct": round(100 * sum(1 for b in beats if b) / len(beats), 1) if beats else None,
        "legs_with_clv": len(beats),
        "record_by_market": by_market,
        "record_by_type": by_type,
        "current_streak": _current_streak(settled),
    }


def _current_streak(settled: list[Bet]) -> int:
    """+N consecutive wins / -N consecutive losses by most-recent placed order."""
    ordered = sorted(settled, key=lambda b: b.placed_at, reverse=True)
    streak = 0
    sign = 0
    for b in ordered:
        if b.status == WON:
            cur = 1
        elif b.status == LOST:
            cur = -1
        else:
            continue  # pushes don't break a streak
        if sign == 0:
            sign = cur
            streak = cur
        elif cur == sign:
            streak += cur
        else:
            break
    return streak
