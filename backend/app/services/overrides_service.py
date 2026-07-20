"""Admin override layer — CRUD + read-time application helpers.

The model pipelines never see overrides; they keep producing raw output.
Response-building code calls the ``apply_*`` helpers here as a final pass, so
one adjustment propagates everywhere that number is consumed (odds edges,
Sparky context, Prop Finder over-probs, start/sit tiers, compare, fantasy
means) without any pipeline knowing overrides exist.

Cache strategy
--------------
Downstream services cache expensive boards. Rather than hunting down and
deleting every cached key on a write, each hooked cache key embeds
``version(db)`` — a token derived from ``count + max(updated_at)`` of the
overrides table. Any write changes the token, so stale entries are simply
never read again and age out via TTL. The token itself is cached for
``_VERSION_TTL`` seconds and force-refreshed on every write.
"""
from __future__ import annotations

import math
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..cache import cache
from ..models.admin_override import (
    ENTITY_TYPES,
    GAME_FIELDS,
    PLAYER_INPUT_FIELDS,
    TEAM_INPUT_FIELDS,
    AdminOverride,
)
from . import player_projection_engine as engine

_VERSION_KEY = "admin_overrides:version"
_VERSION_TTL = 15  # seconds; writes refresh it immediately
_MAP_TTL = 15

SCORING_FANTASY_FIELDS = tuple(f"fantasy_points_{fmt}" for fmt in engine.SCORING_FORMATS)


# ---- Version token (cache-buster) -------------------------------------------

def _compute_version(db: Session) -> str:
    n, latest = db.query(
        func.count(AdminOverride.id), func.max(AdminOverride.updated_at)
    ).one()
    if not n:
        return "ov0"
    stamp = latest.isoformat() if latest is not None else "0"
    return f"ov{n}-{stamp}"


def version(db: Session) -> str:
    """Token to embed in downstream cache keys. Changes on any override write.

    Fails open: if the overrides table can't be read (pre-migration deploy,
    stubbed test session), the layer behaves as "no overrides" instead of
    taking the projection endpoints down with it.
    """
    from . import param_registry

    v = cache.get(_VERSION_KEY)
    if not isinstance(v, str):
        try:
            v = _compute_version(db)
        except Exception:  # noqa: BLE001 — override layer must never break reads
            v = "ov0"
        else:
            cache.set(_VERSION_KEY, v, _VERSION_TTL)
    # Compound token: any model-param write also busts every downstream
    # projection cache keyed on this version, so parameter tuning is live
    # within seconds on all boards without a separate invalidation sweep.
    return f"{v}+{param_registry.version(db)}"


def _refresh_version(db: Session) -> None:
    cache.set(_VERSION_KEY, _compute_version(db), _VERSION_TTL)


# ---- CRUD --------------------------------------------------------------------

def list_overrides(
    db: Session,
    entity_type: str | None = None,
    entity_id: str | None = None,
    season: int | None = None,
    week: int | None = None,
) -> list[dict[str, Any]]:
    q = db.query(AdminOverride)
    if entity_type:
        q = q.filter(AdminOverride.entity_type == entity_type)
    if entity_id:
        q = q.filter(AdminOverride.entity_id == entity_id)
    if season is not None:
        q = q.filter(AdminOverride.season == season)
    if week is not None:
        q = q.filter(AdminOverride.week == week)
    return [o.to_dict() for o in q.order_by(AdminOverride.updated_at.desc()).all()]


def upsert_override(
    db: Session,
    *,
    entity_type: str,
    entity_id: str,
    field: str,
    value: float,
    season: int | None = None,
    week: int | None = None,
    original_value: float | None = None,
    note: str = "",
    created_by: str = "",
) -> dict[str, Any]:
    if entity_type not in ENTITY_TYPES:
        raise ValueError(f"entity_type must be one of {ENTITY_TYPES}")
    if entity_type == "game" and field not in GAME_FIELDS:
        raise ValueError(f"game field must be one of {GAME_FIELDS}")
    if entity_type == "team":
        if field not in TEAM_INPUT_FIELDS:
            raise ValueError(f"team field must be one of {TEAM_INPUT_FIELDS}")
        if week is not None:
            raise ValueError("team input overrides are season-scoped (week must be null)")
    if entity_type == "player" and field in PLAYER_INPUT_FIELDS and week is not None:
        raise ValueError(f"'{field}' is a season-scoped input lever (week must be null)")
    if not math.isfinite(float(value)):
        raise ValueError("value must be a finite number")
    if field in ("pass_rate", "target_share", "rush_share", "snap_rate", "availability") and not (
        0.0 < float(value) <= 1.0
    ):
        raise ValueError(f"'{field}' is a share in (0, 1]")

    row = (
        db.query(AdminOverride)
        .filter(
            AdminOverride.entity_type == entity_type,
            AdminOverride.entity_id == entity_id,
            AdminOverride.season == season,
            AdminOverride.week == week,
            AdminOverride.field == field,
        )
        .first()
    )
    if row is None:
        row = AdminOverride(
            entity_type=entity_type,
            entity_id=entity_id,
            season=season,
            week=week,
            field=field,
            value=float(value),
            original_value=original_value,
            note=note or "",
            created_by=created_by or "",
        )
        db.add(row)
    else:
        row.value = float(value)
        # Keep the FIRST model snapshot; only fill if it was never captured.
        if row.original_value is None and original_value is not None:
            row.original_value = original_value
        if note:
            row.note = note
        if created_by:
            row.created_by = created_by
    from . import audit_service
    audit_service.record(
        db, actor=created_by or "", action="override_set", target_type="override",
        target_key=f"{entity_type}:{entity_id}:{field}",
        old_value=original_value, new_value=float(value), note=note or "",
        context={"season": season, "week": week},
    )
    db.commit()
    db.refresh(row)
    _refresh_version(db)
    return row.to_dict()


def delete_override(db: Session, override_id: int, actor: str = "") -> bool:
    row = db.get(AdminOverride, override_id)
    if row is None:
        return False
    from . import audit_service
    audit_service.record(
        db, actor=actor, action="override_delete", target_type="override",
        target_key=f"{row.entity_type}:{row.entity_id}:{row.field}",
        old_value=row.value, new_value=row.original_value,
        context={"season": row.season, "week": row.week},
    )
    db.delete(row)
    db.commit()
    _refresh_version(db)
    return True


# ---- Lookup maps (short-cached) ----------------------------------------------

def _scoped_map(
    db: Session, entity_type: str, season: int, week: int | None,
) -> dict[str, dict[str, float]]:
    """{entity_id: {field: value}} for one (entity_type, season, week) scope."""
    key = f"admin_overrides:map:{entity_type}:{season}:{week}:{version(db)}"
    cached = cache.get(key)
    if isinstance(cached, dict):
        return cached
    try:
        rows = (
            db.query(AdminOverride)
            .filter(
                AdminOverride.entity_type == entity_type,
                AdminOverride.season == season,
                AdminOverride.week == week,
            )
            .all()
        )
    except Exception:  # noqa: BLE001 — fail open, see version()
        return {}
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        out.setdefault(r.entity_id, {})[r.field] = r.value
    cache.set(key, out, _MAP_TTL)
    return out


def player_overrides_by_week(
    db: Session, season: int,
) -> dict[tuple[str, int], dict[str, float]]:
    """{(player_id, week): {field: value}} for every week-scoped player
    override in a season. Used by multi-week responses (player pages)."""
    key = f"admin_overrides:pmap:{season}:{version(db)}"
    cached = cache.get(key)
    if isinstance(cached, dict):
        return cached
    try:
        rows = (
            db.query(AdminOverride)
            .filter(
                AdminOverride.entity_type == "player",
                AdminOverride.season == season,
                AdminOverride.week.isnot(None),
            )
            .all()
        )
    except Exception:  # noqa: BLE001 — fail open, see version()
        return {}
    out: dict[tuple[str, int], dict[str, float]] = {}
    for r in rows:
        out.setdefault((r.entity_id, int(r.week)), {})[r.field] = r.value
    cache.set(key, out, _MAP_TTL)
    return out


def game_overrides(db: Session, season: int, week: int | None) -> dict[str, dict[str, float]]:
    return _scoped_map(db, "game", season, week)


def player_week_overrides(db: Session, season: int, week: int | None) -> dict[str, dict[str, float]]:
    return _scoped_map(db, "player", season, week)


def player_season_overrides(db: Session, season: int) -> dict[str, dict[str, float]]:
    """Season-scoped (week IS NULL) player overrides — leaderboard rank pins etc."""
    return _scoped_map(db, "player", season, None)


def team_input_overrides(db: Session, season: int) -> dict[str, dict[str, float]]:
    """{team_id: {pace|yards_per_play|pass_rate|points_per_game: value}} —
    season-scoped model-input levers (coaching/scheme changes)."""
    return _scoped_map(db, "team", season, None)


def player_input_overrides(db: Session, season: int) -> dict[str, dict[str, float]]:
    """{player_id: {usage lever: value}} — the PLAYER_INPUT_FIELDS subset of
    season-scoped player overrides (rank pins etc. filtered out)."""
    raw = _scoped_map(db, "player", season, None)
    out: dict[str, dict[str, float]] = {}
    for pid, fields in raw.items():
        levers = {f: v for f, v in fields.items() if f in PLAYER_INPUT_FIELDS}
        if levers:
            out[pid] = levers
    return out


# ---- Application helpers -------------------------------------------------------

def apply_game_prediction(pred: dict[str, Any], ov: dict[str, float]) -> None:
    """Mutate one ``predict_game``-shaped dict coherently.

    Overriding the spread moves the expected margin and per-team scores;
    overriding the total re-splits scores around the (possibly overridden)
    margin; overriding home_win_prob rebalances both probs. Dependent fields
    stay internally consistent so downstream edge math doesn't see a
    contradictory row.
    """
    spread = float(ov.get("predicted_spread", pred.get("predicted_spread", 0.0)))
    total = float(ov.get("predicted_total", pred.get("predicted_total", 0.0)))
    margin = -spread  # negative spread = home favored

    if "predicted_spread" in ov or "predicted_total" in ov:
        pred["predicted_spread"] = round(spread, 1)
        pred["predicted_total"] = round(total, 1)
        pred["predicted_home_score"] = round((total + margin) / 2, 1)
        pred["predicted_away_score"] = round((total - margin) / 2, 1)
        dist = pred.get("distribution")
        if isinstance(dist, dict):
            dist["expected_margin"] = round(margin, 1)

    if "home_win_prob" in ov:
        p = min(0.999, max(0.001, float(ov["home_win_prob"])))
        pred["home_win_prob"] = round(p, 3)
        pred["away_win_prob"] = round(1 - p, 3)
        dist = pred.get("distribution")
        if isinstance(dist, dict):
            dist["home_win_prob"] = round(p, 3)
    elif "predicted_spread" in ov:
        # Spread moved but prob untouched — recompute prob from the new margin
        # with the same sigma so the two stay coherent.
        sigma = float(pred.get("margin_sd") or 13.0)
        p = engine.stat_over_prob(margin, sigma, 0.0)
        pred["home_win_prob"] = round(p, 3)
        pred["away_win_prob"] = round(1 - p, 3)
        dist = pred.get("distribution")
        if isinstance(dist, dict):
            dist["home_win_prob"] = round(p, 3)


def apply_week_game_overrides(
    db: Session, season: int, week: int | None, games: list[dict[str, Any]],
) -> None:
    """Final pass over ``predict_week`` rows (id + prediction shape)."""
    if week is None or not games:
        return
    ovs = game_overrides(db, season, week)
    if not ovs:
        return
    for g in games:
        ov = ovs.get(str(g.get("id") or ""))
        if ov and isinstance(g.get("prediction"), dict):
            apply_game_prediction(g["prediction"], ov)


def apply_stat_projection(
    projected: dict[str, Any], stat: str, value: float,
) -> None:
    """Recenter one projected-stat dict on a hand-set mean, keeping the model's
    week-to-week sd so intervals/over-probs stay honest around the new center."""
    mean = max(0.0, float(value))
    sd = float(projected.get("sd") or 0.0)
    lo50, hi50 = engine.stat_interval(mean, sd, 0.50)
    lo80, hi80 = engine.stat_interval(mean, sd, 0.80)
    projected["mean"] = round(mean, 2)
    projected["predicted"] = _round_like(stat, mean)
    projected["low"] = _round_like(stat, lo50)
    projected["high"] = _round_like(stat, hi50)
    projected["interval_80"] = [_round_like(stat, lo80), _round_like(stat, hi80)]
    if "anytime_prob" in projected:
        projected["anytime_prob"] = round(engine.anytime_td_prob(mean), 3)


def apply_player_game_overrides(
    ov: dict[str, float],
    predicted: dict[str, dict[str, Any]],
    stat_means: dict[str, float],
) -> None:
    """Apply one player-week override dict to a computed stat kit in place.

    Stat fields recenter distributions AND flow into ``stat_means``, so
    fantasy points computed from the means pick the adjustment up
    automatically.
    """
    for field, value in ov.items():
        if field in predicted:
            apply_stat_projection(predicted[field], field, value)
            stat_means[field] = float(predicted[field]["mean"])


def apply_fantasy_overrides(
    ov: dict[str, float], fantasy: dict[str, dict[str, Any]],
) -> None:
    """Direct fantasy-points overrides (``fantasy_points_<fmt>``) on top of
    whatever the (possibly stat-adjusted) means produced."""
    for fmt in engine.SCORING_FORMATS:
        v = ov.get(f"fantasy_points_{fmt}")
        if v is None or fmt not in fantasy:
            continue
        f = fantasy[fmt]
        f["mean"] = round(max(0.0, float(v)), 2)
        sd = float(f.get("sd") or 0.0)
        if "p10" in f:
            f["p10"] = round(engine.stat_quantile(f["mean"], sd, 0.10), 1)
        if "p90" in f:
            f["p90"] = round(engine.stat_quantile(f["mean"], sd, 0.90), 1)


def apply_rank_pins(
    rows: list[dict[str, Any]],
    pins: dict[str, float],
    id_key: str = "player_id",
) -> list[dict[str, Any]]:
    """Reorder a ranked list so pinned players land at their pinned position
    (1-based). Non-pinned players keep their relative order. Returns a new
    list; caller re-stamps rank numbers afterward."""
    if not pins:
        return rows
    pinned: list[tuple[int, dict[str, Any]]] = []
    rest: list[dict[str, Any]] = []
    for r in rows:
        p = pins.get(str(r.get(id_key)))
        if p is not None:
            pinned.append((max(1, int(p)), r))
        else:
            rest.append(r)
    if not pinned:
        return rows
    pinned.sort(key=lambda t: t[0])
    out = rest
    for pos, r in pinned:
        out.insert(min(len(out), pos - 1), r)
    return out


def _round_like(stat: str, v: float) -> float:
    # Mirrors player_predictions_service._round_for_stat without importing it
    # (that module imports us — avoid the cycle).
    return round(float(v), 1) if v is not None else 0.0
