"""Write path for the model-parameter layer: set / revert / presets.

Reads live in param_registry (specs + resolution); this module owns the
``model_params`` rows, the audit entries, and preset lifecycle. Every write
force-refreshes the registry cache so changes are live within seconds on
every replica.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ..logging_config import get_logger
from ..models.model_param import ModelParam, ModelParamPreset
from . import audit_service, param_registry

log = get_logger(__name__)


def _bump() -> None:
    param_registry.invalidate()


# ---- Listing ----------------------------------------------------------------


def list_params(db: Session) -> dict[str, Any]:
    """Full registry for the admin UI: spec + default + current + override info."""
    rows = {r.key: r for r in db.query(ModelParam).all()}
    cats: dict[str, dict[str, Any]] = {
        cid: {"id": cid, **meta, "params": []}
        for cid, meta in param_registry.CATEGORIES.items()
    }
    for key, spec in param_registry.REGISTRY.items():
        row = rows.get(key)
        current = spec.coerce(row.value) if row is not None else spec.default
        entry = {
            **spec.to_dict(),
            "value": current,
            "is_overridden": row is not None,
            "note": row.note if row else "",
            "updated_by": row.updated_by if row else "",
            "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
        }
        cats.setdefault(spec.category, {"id": spec.category, "label": spec.category,
                                        "description": "", "params": []})
        cats[spec.category]["params"].append(entry)
    return {
        "categories": [c for c in cats.values() if c["params"]],
        "overridden_count": sum(1 for k in rows if k in param_registry.REGISTRY),
        "total_count": len(param_registry.REGISTRY),
    }


# ---- Set / revert -----------------------------------------------------------


def set_param(
    db: Session, *, key: str, value: float, actor: str, note: str = "",
) -> dict[str, Any]:
    spec = param_registry.validate(key, value)
    value = spec.coerce(value)
    row = db.query(ModelParam).filter(ModelParam.key == key).one_or_none()
    old = spec.coerce(row.value) if row is not None else spec.default
    if row is None:
        row = ModelParam(key=key, value=value, note=note, updated_by=actor)
        db.add(row)
    else:
        row.value = value
        row.note = note
        row.updated_by = actor
    audit_service.record(
        db, actor=actor, action="param_set", target_type="param", target_key=key,
        old_value=old, new_value=value, note=note,
        context={"default": spec.default, "was_overridden": old != spec.default},
    )
    db.commit()
    _bump()
    return {"key": key, "value": value, "default": spec.default,
            "is_overridden": abs(value - spec.default) > 1e-12}


def revert_param(db: Session, *, key: str, actor: str, note: str = "") -> dict[str, Any]:
    spec = param_registry.REGISTRY.get(key)
    if spec is None:
        raise ValueError(f"unknown model param: {key}")
    row = db.query(ModelParam).filter(ModelParam.key == key).one_or_none()
    if row is None:
        return {"key": key, "value": spec.default, "is_overridden": False}
    old = spec.coerce(row.value)
    db.delete(row)
    audit_service.record(
        db, actor=actor, action="param_revert", target_type="param", target_key=key,
        old_value=old, new_value=spec.default, note=note,
    )
    db.commit()
    _bump()
    return {"key": key, "value": spec.default, "is_overridden": False}


def revert_all(db: Session, *, actor: str, note: str = "") -> dict[str, Any]:
    rows = db.query(ModelParam).all()
    reverted = []
    for row in rows:
        spec = param_registry.REGISTRY.get(row.key)
        old = spec.coerce(row.value) if spec else row.value
        reverted.append(row.key)
        db.delete(row)
        audit_service.record(
            db, actor=actor, action="param_revert", target_type="param",
            target_key=row.key, old_value=old,
            new_value=spec.default if spec else None, note=note,
        )
    audit_service.record(
        db, actor=actor, action="params_revert_all", target_type="param",
        target_key="*", note=note, context={"count": len(reverted)},
    )
    db.commit()
    _bump()
    return {"reverted": reverted}


def bulk_set(
    db: Session, *, changes: dict[str, float], actor: str, note: str = "",
    action: str = "param_set",
) -> dict[str, Any]:
    """Validate everything first (all-or-nothing), then apply atomically."""
    for k, v in changes.items():
        param_registry.validate(k, v, pending=changes)
    applied: dict[str, float] = {}
    for k, v in changes.items():
        spec = param_registry.REGISTRY[k]
        v = spec.coerce(v)
        row = db.query(ModelParam).filter(ModelParam.key == k).one_or_none()
        old = spec.coerce(row.value) if row is not None else spec.default
        if abs(v - spec.default) <= 1e-12:
            if row is not None:
                db.delete(row)
        elif row is None:
            db.add(ModelParam(key=k, value=v, note=note, updated_by=actor))
        else:
            row.value, row.note, row.updated_by = v, note, actor
        if abs(old - v) > 1e-12:
            audit_service.record(
                db, actor=actor, action=action, target_type="param", target_key=k,
                old_value=old, new_value=v, note=note,
            )
        applied[k] = v
    db.commit()
    _bump()
    return {"applied": applied}


# ---- Presets ----------------------------------------------------------------


def list_presets(db: Session) -> list[dict[str, Any]]:
    return [p.to_dict() for p in
            db.query(ModelParamPreset).order_by(ModelParamPreset.name).all()]


def save_preset(
    db: Session, *, name: str, actor: str, description: str = "",
    params: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Snapshot a configuration. Default: current deviations-from-default."""
    name = name.strip()
    if not name:
        raise ValueError("preset name is required")
    snapshot = params if params is not None else param_registry.overrides_map()
    for k, v in snapshot.items():
        param_registry.validate(k, v, pending=snapshot)
    row = db.query(ModelParamPreset).filter(ModelParamPreset.name == name).one_or_none()
    if row is None:
        row = ModelParamPreset(name=name, description=description,
                               params_json=json.dumps(snapshot), created_by=actor)
        db.add(row)
    else:
        row.description = description or row.description
        row.params_json = json.dumps(snapshot)
    audit_service.record(
        db, actor=actor, action="preset_save", target_type="preset", target_key=name,
        note=description, context={"param_count": len(snapshot)},
    )
    db.commit()
    return row.to_dict()


def apply_preset(db: Session, *, name: str, actor: str, note: str = "") -> dict[str, Any]:
    """Make the preset the whole configuration: set its keys, revert the rest."""
    row = db.query(ModelParamPreset).filter(ModelParamPreset.name == name).one_or_none()
    if row is None:
        raise ValueError(f"preset not found: {name}")
    target = row.params()
    for k, v in target.items():
        param_registry.validate(k, v, pending=target)

    current = {r.key: r for r in db.query(ModelParam).all()}
    changed: dict[str, float] = {}
    # Revert keys not in the preset.
    for k, r in current.items():
        if k not in target:
            spec = param_registry.REGISTRY.get(k)
            old = spec.coerce(r.value) if spec else r.value
            db.delete(r)
            changed[k] = spec.default if spec else old
            audit_service.record(
                db, actor=actor, action="param_revert", target_type="param",
                target_key=k, old_value=old,
                new_value=spec.default if spec else None,
                note=f"preset apply: {name}",
            )
    # Set preset keys.
    for k, v in target.items():
        spec = param_registry.REGISTRY[k]
        v = spec.coerce(v)
        r = current.get(k)
        old = spec.coerce(r.value) if r is not None else spec.default
        if r is None:
            db.add(ModelParam(key=k, value=v, note=f"preset: {name}", updated_by=actor))
        else:
            r.value, r.note, r.updated_by = v, f"preset: {name}", actor
        if abs(old - v) > 1e-12:
            changed[k] = v
            audit_service.record(
                db, actor=actor, action="param_set", target_type="param",
                target_key=k, old_value=old, new_value=v,
                note=f"preset apply: {name}",
            )
    audit_service.record(
        db, actor=actor, action="preset_apply", target_type="preset", target_key=name,
        note=note, context={"changed_count": len(changed)},
    )
    db.commit()
    _bump()
    return {"applied": name, "changed": changed}


def delete_preset(db: Session, *, name: str, actor: str) -> bool:
    row = db.query(ModelParamPreset).filter(ModelParamPreset.name == name).one_or_none()
    if row is None:
        return False
    db.delete(row)
    audit_service.record(
        db, actor=actor, action="preset_delete", target_type="preset", target_key=name,
    )
    db.commit()
    return True
