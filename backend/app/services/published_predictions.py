"""Store and refresh one qb-ridge-v5 prediction per game id.

Readers never recompute. A future game with no row may use one labeled
full-v5 fallback on the request path. A game whose kickoff has passed, or
whose kickoff is missing, is never filled that way. The legacy
``epa-elo-ctx-v4`` path is not a serving fallback.

The headline math, including the market blend and ``market.w_cap`` 0.92,
stays in ``predictions_service._predict_week_uncached``. This module only
decides when that payload is stored and who may read it. A separate change
will remove the blend; it should edit that call, not this publication table.

Refresh triggers
----------------
* ``process_start`` — every deploy, web and worker, off the request path
* ``scheduler_interval`` — worker, every 10 minutes, coalesced
* ``stale_read`` — a read saw a future row whose revision lags
* ``elo_rebuild``, ``context_refresh``, ``odds_refresh`` — those jobs just finished

Input revision
--------------
The hash covers the inputs that actually move a published headline, plus the
ratings and schedule identity the product treats as model inputs. See
``INPUT_DEPENDENCIES``. Weather is not a stable row; the 10-minute refresh
picks it up by republishing future games older than ``FRESH_FOR``.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from ..logging_config import get_logger
from ..models.game import Game
from ..models.published_prediction import PredictionRefreshLease, PublishedGamePrediction
from .predictions_service import PREDICTION_MODEL_VERSION

log = get_logger(__name__)

# How long a lease blocks a second refresh. The holder does not keep a row
# lock for this long; the timestamp is the only claim.
LEASE_TTL = timedelta(minutes=12)
# Future games younger than this, on the current revision, are left alone
# except on a deploy refresh.
FRESH_FOR = timedelta(minutes=10)
MAX_WEEKS_PER_RUN = 2
MAX_GAMES_PER_RUN = 32
PUBLISH_ATTEMPTS = 3
SCHEDULER_INTERVAL_MINUTES = 10

# Keys whose effective values enter the revision. ``market.w_cap`` is the
# blend ceiling (default 0.92). Listing it here does not change it.
MARKET_PARAM_KEYS = (
    "market.w_base",
    "market.w_per_source",
    "market.w_cap",
    "market.edge_beta",
    "market.edge_beta_total",
    "market.kalshi_book_equiv",
    "market.line_lookback_hours",
)
DIST_PARAM_KEYS = (
    "dist.conditional_sigma_enabled",
    "game.league_avg_points",
)

INPUT_DEPENDENCIES = (
    "model_version",
    "headline_pipeline",
    "v5.rows_version",
    "v5.enabled",
    "v5.params",
    "v5.margin_coefs",
    "v5.margin_coefs_no_mkt",
    "v5.team_game_rows.updated_at",
    *MARKET_PARAM_KEYS,
    *DIST_PARAM_KEYS,
    "param_registry.version",
    "elo.ratings",
    "team_context.captured_at",
    "player_availability.captured_at",
    "admin_overrides.updated_at",
    "games.schedule",
    "odds_lines.updated_at",
    "odds_snapshots.captured_at",
)

REFRESH_TRIGGERS = (
    "process_start",
    "scheduler_interval",
    "stale_read",
    "elo_rebuild",
    "context_refresh",
    "odds_refresh",
)

ComputeWeek = Callable[[Session, int, int], Awaitable[dict[str, Any]]]

_background_enabled = False
_refresh_task: asyncio.Task | None = None
_run_again = False


def enable_background_refresh() -> None:
    """Allow stale reads to schedule a refresh. Tests leave this off."""
    global _background_enabled
    _background_enabled = True


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def week_cache_key(
    season: int,
    week: int | None,
    model_version: str,
    revision: str,
    generation: int,
) -> str:
    week_part = week if week is not None else "next"
    return f"{season}:{week_part}:{model_version}:{revision}:g{int(generation)}"


def h2h_cache_prefix(team_a: str, team_b: str, season: int, model_version: str) -> str:
    return f"{team_a}:{team_b}:{season}:{model_version}:"


def h2h_cache_key(
    team_a: str,
    team_b: str,
    season: int,
    final_count: int,
    model_version: str,
    revision: str,
    generation: int,
) -> str:
    """Model, input revision, and the publication generation actually stored."""
    prefix = h2h_cache_prefix(team_a, team_b, season, model_version)
    return f"{prefix}f{final_count}:{revision}:g{int(generation)}"


# Statuses that mean the game has started or is over. A future start_time
# does not keep the number open once the feed says the game is live or final.
LOCKED_GAME_STATUSES = (
    "final",
    "in",
    "live",
    "in progress",
    "status_final",
    "complete",
    "finished",
    "post",
)


def publication_cutoff_sql(dialect: str, alias: str = "g") -> str:
    """Wall-clock kickoff test used inside the publication statement.

    Postgres ``now()`` is frozen at transaction start. A writer that blocks on
    the publication lock across kickoff would still see the pre-kickoff
    ``now()`` and the guard would pass. ``clock_timestamp()`` is the clock at
    statement execution, after the lock wait.
    """
    if dialect == "postgresql":
        return f"{alias}.start_time > clock_timestamp()"
    return f"julianday({alias}.start_time) > julianday('now')"


def _status_list_sql() -> str:
    return ", ".join("'" + status.replace("'", "''") + "'" for status in LOCKED_GAME_STATUSES)


def _publishable_predicate_sql(dialect: str, alias: str = "g") -> str:
    cutoff = publication_cutoff_sql(dialect, alias)
    statuses = _status_list_sql()
    return (
        f"lower(coalesce({alias}.status, '')) NOT IN ({statuses}) "
        f"AND ({alias}.start_time IS NULL OR {cutoff})"
    )


def _publishable_exists_sql(dialect: str) -> str:
    return f"""
        EXISTS (
            SELECT 1 FROM games g
            WHERE g.id = :game_id
              AND {_publishable_predicate_sql(dialect, "g")}
        )
    """


def serving_state(status: str | None, kickoff: datetime | None, now: datetime) -> str:
    """``future``, ``past``, ``unknown``, or ``started``.

    A live or final status closes the number even when the stored kickoff is
    still in the future. Missing kickoff stays unknown unless that status closed it.
    """
    if (status or "").strip().lower() in LOCKED_GAME_STATUSES:
        return "started"
    return kickoff_state(kickoff, now)


def kickoff_state(kickoff: datetime | None, now: datetime) -> str:
    """``future``, ``past``, or ``unknown``.

    Missing kickoff is unknown, not past: a blank ``start_time`` must not
    freeze the row forever, and it must not be treated as a live game either.
    """
    kick = as_utc(kickoff)
    if kick is None:
        return "unknown"
    if kick <= as_utc(now):
        return "past"
    return "future"


def resolve_kickoff(
    start_time: datetime | None,
    gameday: str | None = None,
    gametime: str | None = None,
    *,
    today: datetime | None = None,
) -> datetime | None:
    """Prefer a real timestamp. A date-only value is a kickoff only off today.

    Today's date with no clock stays unknown so we neither freeze at midnight
    nor run a live model during the game.
    """
    if start_time is not None:
        return as_utc(start_time)
    day = (gameday or "").strip()
    if not day:
        return None
    clock = (gametime or "").strip()
    now = as_utc(today) or datetime.now(UTC)
    if clock:
        try:
            parsed = datetime.strptime(f"{day} {clock}", "%Y-%m-%d %H:%M")
        except ValueError:
            parsed = None
        if parsed is not None:
            return parsed.replace(tzinfo=UTC)
    try:
        on = datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError:
        return None
    if on == now.date():
        return None
    if on < now.date():
        return datetime(on.year, on.month, on.day, 23, 59, tzinfo=UTC)
    return datetime(on.year, on.month, on.day, 0, 0, tzinfo=UTC)


def select_upcoming_meeting(
    meetings: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any] | None:
    """Earliest meeting that has not been played and has not kicked off.

    A played or already-started earlier meeting does not hide the next one.
    Known kickoffs sort ahead of a meeting whose kickoff is missing.
    """
    upcoming: list[dict[str, Any]] = []
    for meeting in meetings:
        if meeting.get("played"):
            continue
        if kickoff_state(meeting.get("kickoff"), now) == "past":
            continue
        upcoming.append(meeting)
    if not upcoming:
        return None

    def sort_key(meeting: dict[str, Any]) -> tuple:
        kick = as_utc(meeting.get("kickoff"))
        week = meeting.get("week")
        week_n = week if isinstance(week, int) else 999
        if kick is None:
            return (1, week_n, "")
        return (0, kick.timestamp(), week_n)

    return min(upcoming, key=sort_key)


def is_full_v5(prediction: dict[str, Any] | None) -> bool:
    if not isinstance(prediction, dict):
        return False
    if prediction.get("model_version") != PREDICTION_MODEL_VERSION:
        return False
    v5 = (prediction.get("inputs") or {}).get("v5") if isinstance(prediction.get("inputs"), dict) else None
    return isinstance(v5, dict) and v5.get("margin") is not None


def label_full_v5(game: dict[str, Any]) -> dict[str, Any] | None:
    """Copy a computed game and mark it as the labeled v5 fallback.

    A legacy payload is dropped. Nothing here changes the headline numbers.
    """
    pred = game.get("prediction")
    if not is_full_v5(pred):
        return None
    out = copy.deepcopy(game)
    labeled = dict(out["prediction"])
    labeled["prediction_source"] = "v5_fallback"
    labeled["model_version"] = PREDICTION_MODEL_VERSION
    out["prediction"] = labeled
    out["prediction_source"] = "v5_fallback"
    out["model_version"] = PREDICTION_MODEL_VERSION
    out["unavailable_reason"] = None
    return out


def unavailable_game(game: dict[str, Any], reason: str) -> dict[str, Any]:
    """Keep the matchup on the slate without inventing a v4 number."""
    return {
        "id": str(game.get("id") or game.get("game_id") or ""),
        "season": game.get("season"),
        "week": game.get("week"),
        "gameday": str(game.get("gameday") or ""),
        "gametime": str(game.get("gametime") or ""),
        "home_team_id": game.get("home_team_id") or game.get("home_team"),
        "away_team_id": game.get("away_team_id") or game.get("away_team"),
        "home_score": game.get("home_score"),
        "away_score": game.get("away_score"),
        "prediction": None,
        "prediction_source": "unavailable",
        "unavailable_reason": reason,
        "model_version": PREDICTION_MODEL_VERSION,
    }


def _payload_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


def serve_stored(row: PublishedGamePrediction, live: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the stored payload. Scores may move; the prediction does not."""
    payload = copy.deepcopy(_payload_dict(row.payload))
    if live:
        for key in ("home_score", "away_score", "gameday", "gametime"):
            if live.get(key) is not None:
                payload[key] = live.get(key)
    pred = payload.get("prediction")
    if isinstance(pred, dict):
        pred = dict(pred)
        pred["prediction_source"] = "stored"
        pred["model_version"] = row.model_version
        payload["prediction"] = pred
    payload["prediction_source"] = "stored"
    payload["model_version"] = row.model_version
    payload["input_revision"] = row.input_revision
    payload["headline_pipeline"] = row.headline_pipeline
    payload["prediction_basis"] = row.prediction_basis or (
        payload.get("prediction") or {}
    ).get("prediction_basis")
    payload["frozen"] = bool(row.frozen)
    payload["unavailable_reason"] = None
    return payload


def _db_now(db: Session) -> datetime:
    raw = db.execute(text("SELECT CURRENT_TIMESTAMP")).scalar()
    if isinstance(raw, datetime):
        return as_utc(raw) or datetime.now(UTC)
    if isinstance(raw, str):
        text_value = raw.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text_value)
        except ValueError:
            parsed = datetime.strptime(text_value[:19], "%Y-%m-%d %H:%M:%S")
        return as_utc(parsed) or datetime.now(UTC)
    return datetime.now(UTC)


def _rollback(db: Session) -> None:
    try:
        db.rollback()
    except Exception:  # noqa: BLE001
        return


def _scalar(db: Session, sql: str, params: dict[str, Any] | None = None) -> Any:
    try:
        return db.execute(text(sql), params or {}).scalar()
    except Exception:  # noqa: BLE001 — a missing table must not blank the revision
        _rollback(db)
        return None


def _rows(db: Session, sql: str, params: dict[str, Any] | None = None) -> list[tuple]:
    try:
        return list(db.execute(text(sql), params or {}).all())
    except Exception:  # noqa: BLE001
        _rollback(db)
        return []


def _stamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return (as_utc(value) or value).isoformat()
    return str(value)


def collect_inputs(db: Session, *, season: int | None = None) -> dict[str, Any]:
    """Snapshot of every dependency in ``INPUT_DEPENDENCIES``."""
    from ..utils.seasons import current_or_upcoming_season
    from . import game_model_v5_service, param_registry

    season = season or current_or_upcoming_season()
    params: dict[str, Any] = {
        "model_version": PREDICTION_MODEL_VERSION,
        "headline_pipeline": current_headline_pipeline(),
    }
    try:
        params["v5.rows_version"] = game_model_v5_service.ROWS_VERSION
        params["v5.enabled"] = game_model_v5_service.enabled()
        params["v5.params"] = game_model_v5_service.params()
        params["v5.margin_coefs"] = game_model_v5_service.margin_coefs()
        params["v5.margin_coefs_no_mkt"] = game_model_v5_service.margin_coefs_no_mkt()
    except Exception as exc:  # noqa: BLE001
        params["v5.error"] = type(exc).__name__
    for key in (*MARKET_PARAM_KEYS, *DIST_PARAM_KEYS):
        try:
            params[key] = param_registry.value(key)
        except Exception as exc:  # noqa: BLE001
            params[key] = f"unavailable:{type(exc).__name__}"
    try:
        params["param_registry.version"] = param_registry.version(db)
    except Exception:  # noqa: BLE001
        _rollback(db)
        params["param_registry.version"] = None
    params["v5.team_game_rows.updated_at"] = [
        (key, _stamp(updated))
        for key, updated in _rows(
            db,
            "SELECT key, updated_at FROM model_artifacts WHERE kind = 'v5_team_game_rows'",
        )
    ]
    params["elo.ratings"] = [
        (team, int(season_n), int(week), float(rating))
        for team, season_n, week, rating in _rows(
            db,
            """
            SELECT team_id, season, week, rating
            FROM team_elo_ratings
            WHERE season >= :season
            ORDER BY team_id, season, week
            """,
            {"season": season - 1},
        )
    ]
    params["team_context.captured_at"] = _stamp(_scalar(
        db, "SELECT max(captured_at) FROM team_context_snapshots",
    ))
    params["player_availability.captured_at"] = _stamp(_scalar(
        db, "SELECT max(captured_at) FROM player_availability_snapshots",
    ))
    params["admin_overrides.updated_at"] = _stamp(_scalar(
        db, "SELECT max(updated_at) FROM admin_overrides",
    ))
    params["games.schedule"] = [
        (gid, week, home, away, _stamp(start), status)
        for gid, week, home, away, start, status in _rows(
            db,
            """
            SELECT id, week, home_team_id, away_team_id, start_time, status
            FROM games
            WHERE season = :season AND season_type = 2
            ORDER BY id
            """,
            {"season": season},
        )
    ]
    params["odds_lines.updated_at"] = _stamp(_scalar(db, "SELECT max(updated_at) FROM odds_lines"))
    params["odds_snapshots.captured_at"] = _stamp(_scalar(
        db, "SELECT max(captured_at) FROM odds_snapshots",
    ))
    return params


def input_revision(db: Session, *, season: int | None = None) -> str:
    payload = collect_inputs(db, season=season)
    encoded = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()[:20]


def load_published(db: Session, game_ids: list[str]) -> dict[str, PublishedGamePrediction]:
    if not game_ids:
        return {}
    try:
        rows = db.execute(
            select(PublishedGamePrediction).where(PublishedGamePrediction.game_id.in_(game_ids))
        ).scalars().all()
    except OperationalError:
        _rollback(db)
        return {}
    return {row.game_id: row for row in rows}


def current_headline_pipeline() -> str:
    """Pipeline id stamped on every stored headline.

    Today this is ``market-blend-v1`` because the headline is still blended.
    The constant lives in ``market_service`` so the model-only draft, which
    edits that file, cannot keep the old id by accident without a conflict.
    """
    from . import market_service

    pipeline = getattr(market_service, "HEADLINE_PIPELINE", None)
    if isinstance(pipeline, str) and pipeline:
        return pipeline
    return market_service.BLEND_VERSION


def row_is_current(row: PublishedGamePrediction) -> bool:
    """True when this row is the current model and the current headline step.

    A qb-ridge-v5 row produced by the blend step is current only while the
    blend step is still the headline. After that step changes, the row must
    not be served as the new raw model.
    """
    return (
        row.model_version == PREDICTION_MODEL_VERSION
        and (row.headline_pipeline or "") == current_headline_pipeline()
    )


def publication_generation(db: Session) -> int:
    """Generation committed with the last successful publication. 0 if none."""
    try:
        value = _scalar(db, "SELECT generation FROM prediction_refresh_leases WHERE id = 1")
    except Exception:  # noqa: BLE001
        _rollback(db)
        return 0
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def ensure_publication_lock_row(db: Session) -> bool:
    """The single row every writer locks. False when the table is missing."""
    try:
        row = db.get(PredictionRefreshLease, 1)
    except OperationalError:
        _rollback(db)
        return False
    if row is not None:
        return True
    db.add(PredictionRefreshLease(id=1, generation=0, reason="init"))
    try:
        db.commit()
    except IntegrityError:
        _rollback(db)
    return True


def _stored_payload(game: Game, payload: dict[str, Any]) -> dict[str, Any]:
    stored = copy.deepcopy(payload)
    stored["id"] = game.id
    stored["season"] = game.season
    stored["week"] = game.week
    stored["home_team_id"] = game.home_team_id
    stored["away_team_id"] = game.away_team_id
    pred = dict(stored["prediction"])
    pred["prediction_source"] = "stored"
    pred["model_version"] = PREDICTION_MODEL_VERSION
    stored["prediction"] = pred
    stored["prediction_source"] = "stored"
    stored["model_version"] = PREDICTION_MODEL_VERSION
    return stored


def publish_game(
    db: Session,
    game: Game,
    payload: dict[str, Any],
    revision: str,
    *,
    now: datetime | None = None,
) -> str:
    """Publish one game under the shared publication lock.

    The kickoff and started/final checks are predicates on the write itself,
    using the database wall clock. ``now`` is ignored so a caller cannot
    freeze a Python timestamp from before the lock wait. Postgres ``now()``
    is not the cutoff: it would stay at the start of a transaction that
    blocked across kickoff.
    """
    del now  # the database clock inside the write is the only cutoff
    pred = payload.get("prediction") if isinstance(payload, dict) else None
    if not is_full_v5(pred):
        return "rejected_not_v5"
    if not ensure_publication_lock_row(db):
        return "store_unavailable"
    dialect = db.get_bind().dialect.name
    guard = _publishable_exists_sql(dialect)
    clock = "clock_timestamp()" if dialect == "postgresql" else "datetime('now')"
    false_sql = "FALSE" if dialect == "postgresql" else "0"
    true_sql = "TRUE" if dialect == "postgresql" else "1"
    payload_sql = "CAST(:payload AS JSONB)" if dialect == "postgresql" else ":payload"
    body = json.dumps(_stored_payload(game, payload))
    params = {
        "game_id": game.id,
        "payload": body,
        "model": PREDICTION_MODEL_VERSION,
        "revision": revision,
        "reason": "publish",
        "pipeline": current_headline_pipeline(),
        "basis": (pred or {}).get("prediction_basis"),
    }
    try:
        db.commit()
        if dialect == "postgresql":
            db.execute(text(
                "SELECT id FROM prediction_refresh_leases WHERE id = 1 FOR UPDATE"
            ))
        else:
            # First write of the transaction. A waiter blocks here, then runs
            # the guarded update against rows committed while it waited.
            db.execute(text(
                "UPDATE prediction_refresh_leases SET reason = :reason WHERE id = 1"
            ), params)
        updated = db.execute(text(f"""
            UPDATE published_game_predictions
            SET payload = {payload_sql},
                model_version = :model,
                input_revision = :revision,
                headline_pipeline = :pipeline,
                prediction_basis = :basis,
                season = (SELECT season FROM games WHERE id = :game_id),
                week = (SELECT week FROM games WHERE id = :game_id),
                home_team_id = (SELECT home_team_id FROM games WHERE id = :game_id),
                away_team_id = (SELECT away_team_id FROM games WHERE id = :game_id),
                kickoff_at = (SELECT start_time FROM games WHERE id = :game_id),
                kickoff_unknown = (
                    SELECT CASE WHEN start_time IS NULL THEN {true_sql} ELSE {false_sql} END
                    FROM games WHERE id = :game_id
                ),
                frozen = {false_sql},
                frozen_at = NULL,
                published_at = {clock},
                updated_at = {clock}
            WHERE game_id = :game_id
              AND {guard}
        """), params)
        wrote = updated.rowcount or 0
        if wrote == 0:
            inserted = db.execute(text(f"""
                INSERT INTO published_game_predictions (
                    game_id, season, week, home_team_id, away_team_id,
                    model_version, input_revision, headline_pipeline, prediction_basis,
                    payload, frozen, kickoff_unknown,
                    kickoff_at, published_at, frozen_at, created_at, updated_at
                )
                SELECT
                    g.id, g.season, g.week, g.home_team_id, g.away_team_id,
                    :model, :revision, :pipeline, :basis, {payload_sql}, {false_sql},
                    CASE WHEN g.start_time IS NULL THEN {true_sql} ELSE {false_sql} END,
                    g.start_time, {clock}, NULL, {clock}, {clock}
                FROM games g
                WHERE g.id = :game_id
                  AND {_publishable_predicate_sql(dialect, "g")}
                  AND NOT EXISTS (
                      SELECT 1 FROM published_game_predictions p WHERE p.game_id = :game_id
                  )
            """), params)
            wrote = inserted.rowcount or 0
        if wrote:
            db.execute(text(
                "UPDATE prediction_refresh_leases SET generation = generation + 1 WHERE id = 1"
            ))
            unknown = db.execute(text(
                "SELECT kickoff_unknown FROM published_game_predictions WHERE game_id = :game_id"
            ), {"game_id": game.id}).scalar()
            db.commit()
            return "published_kickoff_unknown" if unknown else "published"
        db.execute(text(f"""
            UPDATE published_game_predictions
            SET frozen = {true_sql},
                frozen_at = COALESCE(frozen_at, {clock}),
                updated_at = {clock}
            WHERE game_id = :game_id
              AND NOT ({guard})
        """), params)
        db.commit()
        return "rejected_after_kickoff"
    except Exception:
        _rollback(db)
        raise


def publish_game_locked(
    db: Session,
    game: Game,
    payload: dict[str, Any],
    revision: str,
    *,
    now: datetime | None = None,
) -> str:
    """Writer entry used by the worker and by admin reruns. One lock."""
    return publish_game(db, game, payload, revision, now=now)


def _lease(db: Session) -> PredictionRefreshLease | None:
    try:
        return db.get(PredictionRefreshLease, 1)
    except OperationalError:
        _rollback(db)
        raise


def try_acquire_lease(db: Session, now: datetime, reason: str) -> str | None:
    """Return a token when this caller owns the refresh, else None."""
    token = uuid.uuid4().hex
    until = as_utc(now) + LEASE_TTL
    try:
        row = _lease(db)
    except OperationalError:
        return None
    if row is None:
        db.add(PredictionRefreshLease(
            id=1, token=token, locked_until=until, reason=reason,
        ))
        try:
            db.commit()
        except IntegrityError:
            _rollback(db)
            return None
        return token
    locked = as_utc(row.locked_until)
    if row.token and locked is not None and locked > as_utc(now):
        return None
    row.token = token
    row.locked_until = until
    row.reason = reason
    db.commit()
    return token


def release_lease(db: Session, token: str, *, resume_after: str | None = None) -> None:
    row = db.get(PredictionRefreshLease, 1)
    if row is None or row.token != token:
        return
    row.token = None
    row.locked_until = _db_now(db)
    if resume_after is not None:
        row.resume_after = resume_after
    db.commit()


def future_games(db: Session, now: datetime, *, season: int | None = None) -> list[Game]:
    """Scheduled games that are still publishable: future, or kickoff unknown."""
    try:
        stmt = select(Game).where(Game.season_type == 2)
        if season is not None:
            stmt = stmt.where(Game.season == season)
        rows = list(db.execute(stmt).scalars().all())
    except OperationalError:
        _rollback(db)
        return []
    out = []
    for game in rows:
        if (game.status or "").strip().lower() in LOCKED_GAME_STATUSES:
            continue
        if game.home_score is not None and game.away_score is not None:
            continue
        if kickoff_state(game.start_time, now) == "past":
            continue
        out.append(game)
    out.sort(key=lambda g: (
        1 if g.start_time is None else 0,
        as_utc(g.start_time).timestamp() if g.start_time is not None else 0,
        g.week or 999,
        g.id,
    ))
    return out


def _should_republish(
    row: PublishedGamePrediction | None,
    revision: str,
    reason: str,
    now: datetime,
) -> bool:
    if row is None or not row_is_current(row):
        return True
    if reason in {"process_start", "deploy"}:
        return True
    if row.input_revision != revision:
        return True
    published = as_utc(row.published_at)
    return published is None or as_utc(now) - published >= FRESH_FOR


def rows_stale(db: Session, revision: str, now: datetime | None = None) -> bool:
    clock = as_utc(now) or datetime.now(UTC)
    games = future_games(db, clock)
    if not games:
        return False
    stored = load_published(db, [g.id for g in games])
    for game in games:
        row = stored.get(game.id)
        if row is None or not row_is_current(row):
            return True
        if row.input_revision != revision:
            return True
    return False


def publication_status(db: Session, now: datetime | None = None) -> dict[str, Any]:
    """Staleness and model divergence for the health payload. Never raises."""
    clock = as_utc(now) or datetime.now(UTC)
    try:
        revision = input_revision(db)
        games = future_games(db, clock)
        stored = load_published(db, [g.id for g in games])
    except Exception as exc:  # noqa: BLE001
        _rollback(db)
        return {
            "ok": False,
            "model_version": PREDICTION_MODEL_VERSION,
            "alerts": [{"code": "publication_store_unavailable", "detail": type(exc).__name__}],
        }
    missing = 0
    stale = 0
    diverged = 0
    pipeline = 0
    current_pipeline = current_headline_pipeline()
    for game in games:
        row = stored.get(game.id)
        if row is None:
            missing += 1
            continue
        if row.model_version != PREDICTION_MODEL_VERSION:
            diverged += 1
            continue
        if (row.headline_pipeline or "") != current_pipeline:
            pipeline += 1
            continue
        if row.input_revision != revision and not row.frozen:
            stale += 1
    alerts = []
    if missing:
        alerts.append({"code": "missing_publication", "games": missing})
    if stale:
        alerts.append({"code": "stale_input_revision", "games": stale})
    if pipeline:
        alerts.append({"code": "stale_pipeline", "games": pipeline})
    if diverged:
        alerts.append({"code": "model_divergence", "games": diverged})
    status = {
        "ok": not alerts,
        "model_version": PREDICTION_MODEL_VERSION,
        "headline_pipeline": current_pipeline,
        "input_revision": revision,
        "future_games": len(games),
        "alerts": alerts,
    }
    if alerts:
        signature = json.dumps(alerts, sort_keys=True)
        if signature != getattr(publication_status, "_last", None):
            publication_status._last = signature  # type: ignore[attr-defined]
            log.warning("prediction_publication_alert", **{k: status[k] for k in ("future_games", "alerts")})
    return status


def note_if_stale(db: Session, revision: str, now: datetime | None = None) -> None:
    if not _background_enabled:
        return
    try:
        stale = rows_stale(db, revision, now)
    except Exception:  # noqa: BLE001
        _rollback(db)
        return
    if stale:
        schedule_refresh("stale_read")


def schedule_refresh(reason: str) -> None:
    """Coalesce background refreshes. No-op until background refresh is enabled."""
    global _refresh_task, _run_again
    if not _background_enabled:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _refresh_task is not None and not _refresh_task.done():
        _run_again = True
        return

    async def _run() -> None:
        global _run_again
        from ..db import SessionLocal
        from .predictions_service import build_v5_week

        await _refresh_with_session(SessionLocal, build_v5_week, reason)
        if _run_again:
            _run_again = False
            await _refresh_with_session(SessionLocal, build_v5_week, reason)

    _refresh_task = loop.create_task(_run())


async def _refresh_with_session(session_factory, compute_week: ComputeWeek, reason: str) -> dict[str, Any]:
    db = session_factory()
    try:
        return await refresh_future_games(db, compute_week=compute_week, reason=reason)
    except Exception as exc:  # noqa: BLE001
        log.warning("prediction_refresh_failed", reason=reason, error=type(exc).__name__)
        return {"status": "error", "error": type(exc).__name__}
    finally:
        db.close()


async def refresh_future_games(
    db: Session,
    *,
    compute_week: ComputeWeek,
    reason: str,
    now: datetime | None = None,
    max_games: int = MAX_GAMES_PER_RUN,
    max_weeks: int = MAX_WEEKS_PER_RUN,
    season: int | None = None,
) -> dict[str, Any]:
    """Republish a bounded slice of future games.

    Compute happens before the write transaction. Each game is published in
    its own short transaction that re-checks kickoff. Overlapping callers
    get ``coalesced`` and do not start a second compute.
    """
    clock = as_utc(now) or datetime.now(UTC)
    try:
        token = try_acquire_lease(db, clock, reason)
    except OperationalError:
        return {"status": "store_unavailable", "published": 0}
    if token is None:
        # Whoever holds the lease is already publishing. A second fit would
        # stack work and can lose the kickoff race. Coalesce instead.
        return {"status": "coalesced", "published": 0, "reason": reason}
    published = 0
    rejected = 0
    failed: list[str] = []
    skipped = 0
    last_id: str | None = None
    try:
        revision = input_revision(db, season=season)
        games = future_games(db, clock, season=season)
        lease = db.get(PredictionRefreshLease, 1)
        resume = lease.resume_after if lease is not None else None
        if resume:
            after = [g for g in games if g.id > resume]
            before = [g for g in games if g.id <= resume]
            games = after + before
        grouped: dict[tuple[int, int], list[Game]] = defaultdict(list)
        order: list[tuple[int, int]] = []
        for game in games:
            key = (game.season, int(game.week or 0))
            if key not in grouped:
                order.append(key)
            grouped[key].append(game)
        weeks_done = 0
        for key in order:
            if weeks_done >= max_weeks or published >= max_games:
                break
            week_games = grouped[key]
            stored = load_published(db, [g.id for g in week_games])
            due = [
                g for g in week_games
                if _should_republish(stored.get(g.id), revision, reason, clock)
            ]
            if not due:
                skipped += len(week_games)
                continue
            payload = await compute_week(db, key[0], key[1])
            by_id = {
                str(item.get("id") or ""): item
                for item in (payload.get("games") or [])
                if is_full_v5(item.get("prediction"))
            }
            weeks_done += 1
            for game in due:
                if published >= max_games:
                    break
                last_id = game.id
                item = by_id.get(game.id)
                if item is None:
                    # Keep the previous row. A missing v5 fit must not delete it.
                    skipped += 1
                    continue
                status = "error"
                for _attempt in range(PUBLISH_ATTEMPTS):
                    try:
                        # Clock is read inside the write, not when compute began.
                        status = publish_game_locked(db, game, item, revision, now=None)
                        break
                    except Exception as exc:  # noqa: BLE001
                        log.warning(
                            "prediction_publish_retry",
                            game_id=game.id,
                            error=type(exc).__name__,
                        )
                        status = "error"
                if status in {"published", "published_kickoff_unknown"}:
                    published += 1
                elif status == "rejected_after_kickoff":
                    rejected += 1
                else:
                    failed.append(game.id)
        return {
            "status": "ok",
            "reason": reason,
            "published": published,
            "rejected_after_kickoff": rejected,
            "failed": failed,
            "skipped_fresh": skipped,
            "input_revision": revision,
        }
    finally:
        try:
            release_lease(db, token, resume_after=last_id)
        except Exception:  # noqa: BLE001
            _rollback(db)
