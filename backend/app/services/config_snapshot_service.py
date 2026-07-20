"""Full tuning-config snapshot — export, import, and status overview.

The admin console has three independent tuning layers:

1. **Global params** (``model_params`` / param_registry) — Elo, market blend,
   player engine, weather, injury, lever mechanics, …
2. **Entity overrides** (``admin_overrides``) — hand-set game/player *outputs*
   (spread, total, stat lines, ranks).
3. **Model-input levers** (also ``admin_overrides``, season-scoped team/player
   fields) — what the model *believes* (pace, usage, defense, availability).

This service packages them into one versioned JSON document so a configuration
can be reviewed at a glance, exported for backup, shared across environments,
or re-imported atomically with a full audit trail.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models.admin_override import PLAYER_INPUT_FIELDS, TEAM_INPUT_FIELDS
from . import model_params_service, overrides_service, param_registry

SNAPSHOT_VERSION = 1


def export_snapshot(db: Session, *, season: int | None = None) -> dict[str, Any]:
    """Full current configuration as a portable JSON document."""
    params = param_registry.overrides_map()
    param_meta = {
        k: {
            "value": v,
            "default": param_registry.REGISTRY[k].default,
            "label": param_registry.REGISTRY[k].label,
            "category": param_registry.REGISTRY[k].category,
        }
        for k, v in params.items()
    }

    all_ovs = overrides_service.list_overrides(db, season=season) if season else (
        overrides_service.list_overrides(db)
    )

    team_inputs: list[dict[str, Any]] = []
    player_inputs: list[dict[str, Any]] = []
    game_outputs: list[dict[str, Any]] = []
    player_outputs: list[dict[str, Any]] = []

    for o in all_ovs:
        et, field = o["entity_type"], o["field"]
        slim = {
            "id": o.get("id"),
            "entity_type": et,
            "entity_id": o["entity_id"],
            "field": field,
            "value": o["value"],
            "original_value": o["original_value"],
            "season": o["season"],
            "week": o["week"],
            "note": o["note"],
            "created_by": o["created_by"],
            "updated_at": o["updated_at"],
        }
        if et == "team" and field in TEAM_INPUT_FIELDS:
            team_inputs.append(slim)
        elif et == "player" and field in PLAYER_INPUT_FIELDS and o["week"] is None:
            player_inputs.append(slim)
        elif et == "game":
            game_outputs.append(slim)
        else:
            player_outputs.append(slim)

    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "season_filter": season,
        "params": params,
        "params_detail": param_meta,
        "team_input_levers": team_inputs,
        "player_input_levers": player_inputs,
        "game_output_overrides": game_outputs,
        "player_output_overrides": player_outputs,
        "counts": {
            "params": len(params),
            "team_input_levers": len(team_inputs),
            "player_input_levers": len(player_inputs),
            "game_output_overrides": len(game_outputs),
            "player_output_overrides": len(player_outputs),
            "total_overrides": len(all_ovs),
        },
    }


def tuning_status(db: Session, *, season: int | None = None) -> dict[str, Any]:
    """Dashboard summary of everything currently tuned."""
    snap = export_snapshot(db, season=season)
    params_by_cat: dict[str, list[dict[str, Any]]] = {}
    for key, meta in snap["params_detail"].items():
        cat = meta["category"]
        params_by_cat.setdefault(cat, []).append({
            "key": key,
            "label": meta["label"],
            "value": meta["value"],
            "default": meta["default"],
            "delta": round(meta["value"] - meta["default"], 6),
        })

    # Most recently updated overrides (top of the change surface).
    all_ovs = (
        snap["team_input_levers"]
        + snap["player_input_levers"]
        + snap["game_output_overrides"]
        + snap["player_output_overrides"]
    )
    all_ovs.sort(key=lambda o: o.get("updated_at") or "", reverse=True)

    return {
        "season_filter": season,
        "version_token": overrides_service.version(db),
        "counts": snap["counts"],
        "params_by_category": [
            {
                "id": cid,
                "label": param_registry.CATEGORIES.get(cid, {}).get("label", cid),
                "params": items,
            }
            for cid, items in params_by_cat.items()
        ],
        "recent_overrides": all_ovs[:25],
        "team_input_levers": snap["team_input_levers"],
        "player_input_levers": snap["player_input_levers"],
        "registry_total": len(param_registry.REGISTRY),
        "categories": [
            {"id": cid, **meta}
            for cid, meta in param_registry.CATEGORIES.items()
        ],
    }


def import_snapshot(
    db: Session,
    *,
    payload: dict[str, Any],
    actor: str,
    note: str = "",
    include_params: bool = True,
    include_overrides: bool = True,
    replace_params: bool = False,
) -> dict[str, Any]:
    """Apply a previously exported snapshot.

    * ``include_params`` / ``include_overrides`` control which layers land.
    * ``replace_params=True`` makes the imported params THE configuration
      (reverts every key not in the payload); default is a merge.
    * Entity overrides are always upserted (never wipe-and-replace) so an
      import never silently deletes hand-set values outside the payload.
    """
    results: dict[str, Any] = {
        "params_applied": {},
        "params_reverted": [],
        "overrides_upserted": 0,
        "errors": [],
    }
    note = note or "config snapshot import"

    if include_params:
        params = payload.get("params") or {}
        if not isinstance(params, dict):
            raise ValueError("payload.params must be an object of {key: number}")
        clean: dict[str, float] = {}
        for k, v in params.items():
            if k not in param_registry.REGISTRY:
                results["errors"].append(f"unknown param skipped: {k}")
                continue
            try:
                clean[k] = float(v)
            except (TypeError, ValueError):
                results["errors"].append(f"non-numeric param skipped: {k}")

        if replace_params:
            from ..models.model_param import ModelParam

            existing = {r.key for r in db.query(ModelParam).all()}
            if clean:
                applied = model_params_service.bulk_set(
                    db, changes=clean, actor=actor, note=note, action="param_set",
                )
                results["params_applied"] = applied.get("applied") or clean
            for k in existing - set(clean):
                try:
                    model_params_service.revert_param(
                        db, key=k, actor=actor, note=f"{note} (replace)",
                    )
                    results["params_reverted"].append(k)
                except ValueError:
                    pass
        elif clean:
            applied = model_params_service.bulk_set(
                db, changes=clean, actor=actor, note=note, action="param_set",
            )
            results["params_applied"] = applied.get("applied") or clean

    if include_overrides:
        # Prefer explicit combined list if provided; else merge all buckets.
        rows: list[dict[str, Any]] = []
        if isinstance(payload.get("overrides"), list):
            rows = payload["overrides"]
        else:
            for key in (
                "team_input_levers",
                "player_input_levers",
                "game_output_overrides",
                "player_output_overrides",
            ):
                chunk = payload.get(key) or []
                if isinstance(chunk, list):
                    rows.extend(chunk)

        for o in rows:
            try:
                overrides_service.upsert_override(
                    db,
                    entity_type=o["entity_type"],
                    entity_id=str(o["entity_id"]),
                    field=o["field"],
                    value=float(o["value"]),
                    season=o.get("season"),
                    week=o.get("week"),
                    original_value=o.get("original_value"),
                    note=o.get("note") or note,
                    created_by=actor,
                )
                results["overrides_upserted"] += 1
            except (KeyError, TypeError, ValueError) as e:
                results["errors"].append(
                    f"override skip {o.get('entity_type')}:{o.get('entity_id')}:"
                    f"{o.get('field')}: {e}"
                )

    from . import audit_service

    audit_service.record(
        db, actor=actor, action="config_import", target_type="config",
        target_key="snapshot",
        note=note,
        context={
            "params": len(results["params_applied"]),
            "params_reverted": len(results["params_reverted"]),
            "overrides": results["overrides_upserted"],
            "errors": len(results["errors"]),
            "replace_params": replace_params,
        },
    )
    db.commit()
    return results
