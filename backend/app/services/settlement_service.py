"""Per-game settlement.

Each final game is its own transaction:

1. Claim it (``settlement_status='settling'``) and commit that claim.
2. Grade still-pending bets on the game and, when a Sparky prediction is
   supplied, upsert one historical result.
3. Mark the game ``settled`` and commit.

If step 2 raises, the transaction rolls back. Legs, payouts, and the
historical row from that attempt are gone. The claim from step 1 stays
``settling``. A later run leaves it alone until ``claimed_at`` is older
than the configured TTL, measured with the database clock, then reclaims
it and logs a warning. The settled write matches the claim token from
this attempt, so a worker whose lease was taken over cannot commit.
A later pass that only grades wagers placed after the game settled
rotates that token: ``UPDATE ... SET claim_token=:new WHERE
claim_token=:observed AND settlement_status='settled'``. A second
runner that read the same token waits, then matches zero rows and
rolls its grades back.
The reset script can clear the claim sooner (dry-run unless ``--apply``;
it requires an explicit non-production APP_ENV and an allowlisted host).

Re-running a game that is already ``settled`` only grades bets that are still
pending (a wager logged after the game ended). It does not rewrite a stored
result or add the profit a second time.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Integer, and_, cast, func, literal_column, or_, select, update
from sqlalchemy.orm import Session

from ..logging_config import get_logger
from ..models.game import Game
from ..models.sparky import SparkyGamePrediction
from . import bet_service
from .sparky_result_store import historical_result_values, upsert_historical_result

log = get_logger(__name__)

SETTLING = "settling"
SETTLED = "settled"


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes. Treat them as UTC when a test compares them."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _claim_ttl() -> timedelta:
    from ..config import get_settings

    seconds = int(get_settings().settlement_claim_ttl_seconds)
    return timedelta(seconds=max(seconds, 1))


def _ttl_seconds() -> int:
    return max(int(_claim_ttl().total_seconds()), 1)


def _dialect_name(db: Session) -> str:
    return db.get_bind().dialect.name


def _lease_deadline(dialect: str, ttl_seconds: int):
    """A database timestamp ``ttl_seconds`` before the database's own now.

    SQLite stores datetimes as naive UTC strings. Binding an aware Python
    datetime and comparing it to that string is not ordered, so the deadline
    is produced by the database in the same shape as ``claimed_at``.
    """
    seconds = max(int(ttl_seconds), 1)
    if dialect == "sqlite":
        return func.datetime(
            literal_column("'now'"),
            literal_column(f"'-{seconds} seconds'"),
        )
    return func.now() - literal_column(f"make_interval(secs => {seconds})")


def _claim_age_sql(dialect: str):
    """Age of ``claimed_at`` in seconds, computed by the database clock."""
    if dialect == "sqlite":
        return cast(
            (
                func.julianday(literal_column("'now'"))
                - func.julianday(Game.claimed_at)
            )
            * 86400,
            Integer,
        )
    return cast(func.extract("epoch", func.now() - Game.claimed_at), Integer)


def claim_game(db: Session, game: Game) -> tuple[str, str | None]:
    """Return ``(status, claim_token)``.

    ``status`` is ``claimed``, ``settled``, or ``stuck``. A won claim
    returns the new token, committed before any grading writes. A game
    that is already ``settled`` returns the token stored on the row, so
    a later grade of late wagers can refuse to commit if that token
    changed. Lease expiry uses the database clock, not the application clock.

    A claim whose ``claimed_at`` is missing or older than the TTL is
    reclaimed by the same conditional UPDATE. The warning names the game
    and how long the previous claim was held.
    """
    dialect = _dialect_name(db)
    deadline = _lease_deadline(dialect, _ttl_seconds())
    status, age, observed = db.execute(
        select(
            Game.settlement_status,
            _claim_age_sql(dialect),
            Game.claim_token,
        ).where(Game.id == game.id)
    ).one()
    if status == SETTLED:
        return "settled", observed

    token = uuid.uuid4().hex
    res = db.execute(
        update(Game)
        .where(Game.id == game.id)
        .where(
            or_(
                Game.settlement_status.is_(None),
                and_(
                    Game.settlement_status == SETTLING,
                    or_(
                        Game.claimed_at.is_(None),
                        Game.claimed_at < deadline,
                    ),
                ),
            )
        )
        .values(
            settlement_status=SETTLING,
            claim_token=token,
            claimed_at=func.now(),
            settlement_started_at=func.coalesce(Game.settlement_started_at, func.now()),
        )
    )
    if (res.rowcount or 0) == 1:
        db.commit()
        db.refresh(game)
        if status == SETTLING:
            log.warning(
                "settlement_claim_expired",
                game_id=game.id,
                claim_age_seconds=None if age is None else int(age),
            )
        return "claimed", token

    db.rollback()
    status, observed = db.execute(
        select(Game.settlement_status, Game.claim_token).where(Game.id == game.id)
    ).one()
    db.refresh(game)
    if status == SETTLED:
        return "settled", observed
    return "stuck", None


def mark_settled(db: Session, game_id: str, token: str) -> bool:
    """Set ``settled`` only if this worker still holds ``token``.

    Returns False when the lease was reclaimed and the token no longer
    matches. The caller rolls back the grading writes in that case.
    """
    if not token:
        return False
    res = db.execute(
        update(Game)
        .where(Game.id == game_id)
        .where(Game.settlement_status == SETTLING)
        .where(Game.claim_token == token)
        .values(
            settlement_status=SETTLED,
            settlement_finished_at=func.now(),
        )
    )
    return (res.rowcount or 0) == 1


def hold_settled_claim(db: Session, game_id: str, token: str) -> bool:
    """Rotate the settled claim token if this worker still holds it.

    Late wagers are graded after the game is settled, so ``mark_settled``
    (which requires ``settling``) does not apply. Two runners can both
    read the same stored token. Setting a new token makes the second
    runner's predicate fail: on Postgres it waits for this row lock,
    re-evaluates after this transaction commits, gets rowcount 0, and
    rolls its grades back. Writing the same token back would still match.
    """
    if not token:
        return False
    rotated = uuid.uuid4().hex
    res = db.execute(
        update(Game)
        .where(Game.id == game_id)
        .where(Game.settlement_status == SETTLED)
        .where(Game.claim_token == token)
        .values(claim_token=rotated)
    )
    return (res.rowcount or 0) == 1


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
            claim_token=None,
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
        claim, token = claim_game(db, game)
        if claim == "stuck":
            db.rollback()
            log.info("game_settlement_stuck", game_id=game.id)
            return _empty(stuck=True)

        # Drop the in-memory game so a flush cannot write settlement columns
        # without the claim-token predicate below.
        db.expire(game)
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
            if not mark_settled(db, game.id, token or ""):
                db.rollback()
                log.warning("settlement_claim_lost", game_id=game.id)
                return _empty(rolled_back=1)
            db.commit()
            db.refresh(game)
            return _empty(
                settled_bets=grades["settled_bets"],
                graded_legs=grades["graded_legs"],
                sparky_writes=sparky_writes,
                sparky_unchanged=sparky_unchanged,
            )

        # Core grade writes do not mark the session dirty. A positive grade
        # count still has to take the token lock before it commits.
        if (
            sparky_writes
            or grades["graded_legs"]
            or grades["settled_bets"]
            or db.dirty
            or db.new
        ):
            if not hold_settled_claim(db, game.id, token or ""):
                db.rollback()
                log.warning("settlement_claim_lost", game_id=game.id)
                return _empty(rolled_back=1)
            db.commit()
            db.refresh(game)
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
