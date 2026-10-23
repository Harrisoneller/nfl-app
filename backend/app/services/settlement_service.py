"""Per-game settlement.

Each final game is its own transaction:

1. Claim it (``settlement_status='settling'``) and commit that claim.
2. Grade still-pending bets on the game and, when a Sparky prediction is
   supplied, upsert one historical result.
3. Mark the game ``settled`` and commit.

If step 2 raises, the transaction rolls back. Legs, payouts, and the
historical row from that attempt are gone. The claim from step 1 stays
``settling``. A later run leaves it alone until ``claimed_at`` is older
than the configured TTL, then reclaims it and logs a warning. The reset
script can clear the claim sooner (dry-run unless ``--apply``; it only
connects to allowlisted local and test hosts).

Re-running a game that is already ``settled`` only grades bets that are still
pending (a wager logged after the game ended). It does not rewrite a stored
result or add the profit a second time.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from ..logging_config import get_logger
from ..models.game import Game
from ..models.sparky import SparkyGamePrediction
from . import bet_service
from .sparky_result_store import historical_result_values, upsert_historical_result

log = get_logger(__name__)

SETTLING = "settling"
SETTLED = "settled"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes. Treat them as UTC so lease math matches."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _claim_ttl() -> timedelta:
    from ..config import get_settings

    seconds = int(get_settings().settlement_claim_ttl_seconds)
    return timedelta(seconds=max(seconds, 1))


def _claim_age_seconds(claimed_at: datetime | None, now: datetime) -> int | None:
    claimed = _as_utc(claimed_at)
    if claimed is None:
        return None
    return int((_as_utc(now) - claimed).total_seconds())


def _lease_expired(claimed_at: datetime | None, cutoff: datetime) -> bool:
    """A missing timestamp is expired so a claim from before the column existed can be taken."""
    claimed = _as_utc(claimed_at)
    if claimed is None:
        return True
    return claimed < _as_utc(cutoff)


def claim_game(
    db: Session,
    game: Game,
    *,
    now: datetime | None = None,
    ttl: timedelta | None = None,
) -> str:
    """Return ``claimed``, ``settled``, or ``stuck``.

    The transition into ``settling`` is a single conditional UPDATE so two
    workers cannot both grade the same game. It is committed before any
    grading writes.

    A claim whose ``claimed_at`` is older than the TTL is reclaimed by that
    same UPDATE. The warning names the game and how long the previous claim
    was held.
    """
    now = now or _now()
    ttl = ttl if ttl is not None else _claim_ttl()
    cutoff = now - ttl
    status, claimed_at = db.execute(
        select(Game.settlement_status, Game.claimed_at).where(Game.id == game.id)
    ).one()
    if status == SETTLED:
        return "settled"
    if status == SETTLING and not _lease_expired(claimed_at, cutoff):
        return "stuck"

    expired = status == SETTLING
    res = db.execute(
        update(Game)
        .where(Game.id == game.id)
        .where(
            or_(
                Game.settlement_status.is_(None),
                and_(
                    Game.settlement_status == SETTLING,
                    or_(Game.claimed_at.is_(None), Game.claimed_at < cutoff),
                ),
            )
        )
        .values(
            settlement_status=SETTLING,
            claimed_at=now,
            settlement_started_at=func.coalesce(Game.settlement_started_at, now),
        )
    )
    if (res.rowcount or 0) == 1:
        db.commit()
        db.refresh(game)
        if expired:
            log.warning(
                "settlement_claim_expired",
                game_id=game.id,
                claim_age_seconds=_claim_age_seconds(claimed_at, now),
            )
        return "claimed"

    db.rollback()
    status, claimed_at = db.execute(
        select(Game.settlement_status, Game.claimed_at).where(Game.id == game.id)
    ).one()
    db.refresh(game)
    if status == SETTLED:
        return "settled"
    return "stuck"


def find_stuck_game_ids(db: Session) -> list[str]:
    """Game ids claimed for settlement and never finished."""
    rows = db.execute(
        select(Game.id)
        .where(Game.settlement_status == SETTLING)
        .order_by(Game.settlement_started_at.asc(), Game.id.asc())
    ).all()
    return [row[0] for row in rows]


def reset_stuck_games(db: Session, game_ids: list[str] | None = None) -> int:
    """Clear the ``settling`` claim so a later settlement can retry.

    Does not change scores, bets, or historical results. The grading
    transaction that followed the claim was rolled back, so those rows are
    not half-written. Caller commits.
    """
    stmt = update(Game).where(Game.settlement_status == SETTLING)
    if game_ids is not None:
        if not game_ids:
            return 0
        stmt = stmt.where(Game.id.in_(game_ids))
    res = db.execute(
        stmt.values(
            settlement_status=None,
            settlement_started_at=None,
            claimed_at=None,
            settlement_finished_at=None,
        )
    )
    return int(res.rowcount or 0)


def _empty(**extra: int | bool) -> dict:
    out = {
        "settled_bets": 0,
        "graded_legs": 0,
        "sparky_writes": 0,
        "sparky_unchanged": 0,
        "rolled_back": 0,
        "stuck": False,
    }
    out.update(extra)
    return out


def _signal_keys(prediction: SparkyGamePrediction) -> list:
    return [
        s.get("key")
        for s in (prediction.signals or [])
        if isinstance(s, dict)
    ]


def settle_game(
    db: Session,
    game: Game,
    *,
    user_id: uuid.UUID | None = None,
    prediction: SparkyGamePrediction | None = None,
    actual_winner: str | None = None,
) -> dict:
    """Grade one game and optionally record its Sparky result. Commits."""
    try:
        claim = claim_game(db, game)
        if claim == "stuck":
            db.rollback()
            log.info("game_settlement_stuck", game_id=game.id)
            return _empty(stuck=True)

        grades = bet_service.apply_game_grades(db, game, user_id=user_id)
        sparky_writes = 0
        sparky_unchanged = 0
        if prediction is not None and actual_winner is not None:
            values = historical_result_values(
                event_id=prediction.event_id,
                slate_date=prediction.slate_date,
                game_id=game.id,
                predicted_winner=prediction.predicted_winner,
                actual_winner=actual_winner,
                confidence_score=prediction.confidence_score,
                classification=prediction.classification,
                signal_keys=_signal_keys(prediction),
                win_prob=prediction.win_prob,
                model_prob=prediction.model_prob,
                market_prob=prediction.market_prob,
            )
            action = upsert_historical_result(db, values)
            if action == "written":
                sparky_writes = 1
            else:
                sparky_unchanged = 1

        if claim == "claimed":
            game.settlement_status = SETTLED
            game.settlement_finished_at = _now()

        if claim == "claimed" or sparky_writes or db.dirty or db.new:
            db.commit()
        else:
            db.rollback()
        return _empty(
            settled_bets=grades["settled_bets"],
            graded_legs=grades["graded_legs"],
            sparky_writes=sparky_writes,
            sparky_unchanged=sparky_unchanged,
        )
    except Exception as exc:  # noqa: BLE001 — one game must not poison the batch
        db.rollback()
        log.warning(
            "game_settlement_rolled_back",
            game_id=getattr(game, "id", None),
            error=str(exc)[:200],
        )
        return _empty(rolled_back=1)


def settle_pending_bets(db: Session, user_id: uuid.UUID) -> dict[str, int]:
    """Settle one user's pending bets, one final game per transaction."""
    pending = bet_service.list_bets(db, user_id, status=bet_service.PENDING)
    game_ids: list[str] = []
    seen: set[str] = set()
    for bet in pending:
        for leg in bet.legs:
            if leg.leg_result != bet_service.PENDING:
                continue
            game = bet_service._find_game(db, leg)
            if game is None or (game.status or "").lower() not in bet_service._FINAL:
                continue
            if game.id not in seen:
                seen.add(game.id)
                game_ids.append(game.id)

    settled_bets = 0
    graded_legs = 0
    games_rolled_back = 0
    games_stuck = 0
    for game_id in sorted(game_ids):
        game = db.get(Game, game_id)
        if game is None:
            continue
        outcome = settle_game(db, game, user_id=user_id)
        settled_bets += outcome["settled_bets"]
        graded_legs += outcome["graded_legs"]
        games_rolled_back += outcome["rolled_back"]
        games_stuck += int(outcome["stuck"])

    try:
        if bet_service.apply_clv_for_open_legs(db, user_id):
            db.commit()
        else:
            db.rollback()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        log.warning("open_leg_clv_rolled_back", error=str(exc)[:200])

    return {
        "settled_bets": settled_bets,
        "graded_legs": graded_legs,
        "pending_scanned": len(pending),
        "games_rolled_back": games_rolled_back,
        "games_stuck": games_stuck,
    }
