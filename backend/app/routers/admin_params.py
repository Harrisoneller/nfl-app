"""Admin-only model-parameter endpoints — the global tuning console.

Backs /admin → Parameters + Change Log + Config Status:

* ``GET    /admin/params``                 registry grouped by category
* ``PUT    /admin/params/values/{key}``    set one param (bounds-validated, audited)
* ``DELETE /admin/params/values/{key}``    revert one param to its code default
* ``POST   /admin/params/bulk``            atomic multi-param write
* ``POST   /admin/params/revert-all``      revert everything
* ``GET    /admin/params/status``          dashboard of all active tuning
* ``GET    /admin/params/snapshot``        portable export (params + overrides)
* ``POST   /admin/params/import``          restore from a snapshot
* ``GET/POST/DELETE /admin/params/presets`` named configuration snapshots
* ``POST   /admin/params/presets/{name}/apply``
* ``GET    /admin/params/audit``           unified change log (params + overrides)
* ``POST   /admin/params/preview``         before→after impact of staged changes

Preview never writes: staged values are applied through a context-local
overlay (param_registry.overlay), the game slate and player board are
recomputed under it, and the response is a per-entity diff. Notes: Elo
ratings themselves are rebuilt by the batch job, so K-factor / season-
regression changes preview as no-ops on spreads until the next rebuild, and
the cached market consensus (~10 min) may lag a Kalshi-weight change.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..deps import get_db, require_admin
from ..models.user import User
from ..services import audit_service, model_params_service, param_registry

router = APIRouter(dependencies=[Depends(require_admin)])

_MAX_PREVIEW_PLAYERS = 250


class ParamSet(BaseModel):
    value: float
    note: str = Field(default="", max_length=500)


class RevertAll(BaseModel):
    note: str = Field(default="", max_length=500)


class PresetSave(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    # Explicit snapshot; None = snapshot current deviations-from-default.
    params: dict[str, float] | None = None


class PresetApply(BaseModel):
    note: str = Field(default="", max_length=500)


class PreviewRequest(BaseModel):
    changes: dict[str, float] = Field(min_length=1)
    season: int | None = None
    week: int | None = Field(default=None, ge=1, le=23)
    scoring: str = Field(default="ppr", pattern="^(ppr|half_ppr|standard)$")
    player_limit: int = Field(default=60, ge=10, le=_MAX_PREVIEW_PLAYERS)


class BulkSetRequest(BaseModel):
    changes: dict[str, float] = Field(min_length=1)
    note: str = Field(default="", max_length=500)


class ConfigImportRequest(BaseModel):
    """Apply a previously exported config snapshot (params and/or overrides)."""
    snapshot: dict[str, Any]
    note: str = Field(default="", max_length=500)
    include_params: bool = True
    include_overrides: bool = True
    # When true, imported params replace the whole config (keys not present
    # revert to defaults). Default is a merge on top of current overrides.
    replace_params: bool = False


# ---- Registry ---------------------------------------------------------------


@router.get("")
def list_params(db: Session = Depends(get_db)):
    return model_params_service.list_params(db)


@router.put("/values/{key:path}")
def set_param(
    key: str,
    body: ParamSet,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    try:
        return model_params_service.set_param(
            db, key=key, value=body.value, actor=admin.email or "", note=body.note,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None


@router.delete("/values/{key:path}")
def revert_param(
    key: str,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    try:
        return model_params_service.revert_param(db, key=key, actor=admin.email or "")
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from None


@router.post("/revert-all")
def revert_all(
    body: RevertAll,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    return model_params_service.revert_all(db, actor=admin.email or "", note=body.note)


@router.post("/bulk")
def bulk_set_params(
    body: BulkSetRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Atomic multi-param write (all-or-nothing bounds + cross-param validation)."""
    try:
        return model_params_service.bulk_set(
            db, changes=body.changes, actor=admin.email or "", note=body.note,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None


# ---- Config snapshot / status -----------------------------------------------


@router.get("/status")
def tuning_status(season: int | None = None, db: Session = Depends(get_db)):
    """Dashboard: every active param override + input lever + output pin."""
    from ..services import config_snapshot_service

    return config_snapshot_service.tuning_status(db, season=season)


@router.get("/snapshot")
def export_snapshot(season: int | None = None, db: Session = Depends(get_db)):
    """Portable JSON of the full tuning config (params + overrides + levers)."""
    from ..services import config_snapshot_service

    return config_snapshot_service.export_snapshot(db, season=season)


@router.post("/import")
def import_snapshot(
    body: ConfigImportRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Restore params and/or entity overrides from an exported snapshot."""
    from ..services import config_snapshot_service

    try:
        return config_snapshot_service.import_snapshot(
            db,
            payload=body.snapshot,
            actor=admin.email or "",
            note=body.note,
            include_params=body.include_params,
            include_overrides=body.include_overrides,
            replace_params=body.replace_params,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None


# ---- Presets ----------------------------------------------------------------


@router.get("/presets")
def list_presets(db: Session = Depends(get_db)):
    return {"presets": model_params_service.list_presets(db)}


@router.post("/presets")
def save_preset(
    body: PresetSave,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    try:
        return model_params_service.save_preset(
            db, name=body.name, actor=admin.email or "",
            description=body.description, params=body.params,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None


@router.post("/presets/{name}/apply")
def apply_preset(
    name: str,
    body: PresetApply,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    try:
        return model_params_service.apply_preset(
            db, name=name, actor=admin.email or "", note=body.note,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from None


@router.delete("/presets/{name}")
def delete_preset(
    name: str,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if not model_params_service.delete_preset(db, name=name, actor=admin.email or ""):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Preset not found")
    return {"deleted": name}


# ---- Change log -------------------------------------------------------------


@router.get("/audit")
def audit_timeline(
    target_type: str | None = None,
    action: str | None = None,
    actor: str | None = None,
    search: str | None = None,
    limit: int = 100,
    before_id: int | None = None,
    db: Session = Depends(get_db),
):
    return audit_service.timeline(
        db, target_type=target_type, action=action, actor=actor,
        search=search, limit=limit, before_id=before_id,
    )


# ---- Impact preview ---------------------------------------------------------


def _game_key_numbers(g: dict[str, Any]) -> dict[str, Any]:
    pred = g.get("prediction") or {}
    return {
        "spread": pred.get("predicted_spread"),
        "total": pred.get("predicted_total"),
        "home_win_prob": pred.get("home_win_prob"),
    }


def _round_delta(a: float | None, b: float | None, nd: int = 3) -> float | None:
    if a is None or b is None:
        return None
    return round(b - a, nd)


@router.post("/preview")
async def preview_impact(body: PreviewRequest, db: Session = Depends(get_db)):
    """Recompute this week's slate + player board under staged param values.

    Nothing is persisted; the staged values live in a context-local overlay
    for the duration of this request only.
    """
    # Validate the whole staged set first (cross-param rules see each other).
    try:
        for k, v in body.changes.items():
            param_registry.validate(k, v, pending=body.changes)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None

    from ..services import player_predictions_service, predictions_service
    from ..utils.seasons import current_or_upcoming_season

    season = body.season or current_or_upcoming_season()

    async def _compute() -> tuple[dict[str, Any], dict[str, Any]]:
        games = await predictions_service.predict_week(db, season, body.week)
        board = await player_predictions_service.weekly_projection_board(
            db, season=season, week=body.week, scoring=body.scoring,
            limit=_MAX_PREVIEW_PLAYERS,
        )
        return games, board

    base_games, base_board = await _compute()
    with param_registry.overlay(body.changes):
        new_games, new_board = await _compute()

    # ---- Game diffs ----
    base_by_id = {g.get("id"): g for g in base_games.get("games", [])}
    game_diffs: list[dict[str, Any]] = []
    for g in new_games.get("games", []):
        b = base_by_id.get(g.get("id"))
        if not b:
            continue
        before, after = _game_key_numbers(b), _game_key_numbers(g)
        game_diffs.append({
            "game_id": g.get("id"),
            "home_team": g.get("home_team_id"),
            "away_team": g.get("away_team_id"),
            "before": before,
            "after": after,
            "delta": {
                "spread": _round_delta(before["spread"], after["spread"], 2),
                "total": _round_delta(before["total"], after["total"], 2),
                "home_win_prob": _round_delta(
                    before["home_win_prob"], after["home_win_prob"], 4,
                ),
            },
        })
    game_diffs.sort(
        key=lambda d: abs(d["delta"]["spread"] or 0) + abs(d["delta"]["total"] or 0),
        reverse=True,
    )

    # ---- Player diffs (top movers by projected fantasy points) ----
    def _board_map(board: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            str(p.get("player_id")): p
            for p in board.get("players") or []
            if p.get("player_id") is not None
        }

    def _pts(p: dict[str, Any]) -> float | None:
        f = (p.get("fantasy") or {}).get(body.scoring) or {}
        return f.get("mean")

    base_players, new_players = _board_map(base_board), _board_map(new_board)
    player_diffs: list[dict[str, Any]] = []
    for pid, np_ in new_players.items():
        bp = base_players.get(pid)
        if not bp:
            continue
        b_pts, n_pts = _pts(bp), _pts(np_)
        if b_pts is None or n_pts is None:
            continue
        delta = float(n_pts) - float(b_pts)
        if abs(delta) < 1e-9:
            continue
        player_diffs.append({
            "player_id": pid,
            "name": np_.get("name"),
            "position": np_.get("position"),
            "team": np_.get("team"),
            "before": round(float(b_pts), 2),
            "after": round(float(n_pts), 2),
            "delta": round(delta, 2),
        })
    player_diffs.sort(key=lambda d: abs(d["delta"]), reverse=True)
    player_diffs = player_diffs[: body.player_limit]

    moved_games = [d for d in game_diffs if any(
        v and abs(v) > 1e-9 for v in d["delta"].values()
    )]
    return {
        "season": season,
        "week": new_games.get("week"),
        "changes": body.changes,
        "summary": {
            "games_evaluated": len(game_diffs),
            "games_moved": len(moved_games),
            "max_spread_delta": max(
                (abs(d["delta"]["spread"] or 0) for d in game_diffs), default=0.0,
            ),
            "max_total_delta": max(
                (abs(d["delta"]["total"] or 0) for d in game_diffs), default=0.0,
            ),
            "players_moved": len(player_diffs),
            "max_player_delta": max(
                (abs(d["delta"]) for d in player_diffs), default=0.0,
            ),
        },
        "games": game_diffs,
        "players": player_diffs,
        "notes": [
            "Elo ratings are rebuilt by the batch job — K-factor and season carry-over changes affect future rebuilds, not this preview.",
            "Market consensus is cached ~10 min; a Kalshi-weight change may take one cache cycle to show fully.",
        ],
    }
