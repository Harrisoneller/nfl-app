"""Admin-only projection override endpoints.

Backs the /admin page: list active overrides, upsert one, revert one.
Every route requires ``require_admin`` (ADMIN_EMAILS allowlist when set,
else the DB is_admin flag) — same gate as Sparky's admin tab.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..deps import get_db, require_admin
from ..models.admin_override import PLAYER_INPUT_FIELDS
from ..models.user import User
from ..services import overrides_service

router = APIRouter(dependencies=[Depends(require_admin)])


class OverrideUpsert(BaseModel):
    entity_type: str = Field(pattern="^(game|player|team)$")
    entity_id: str = Field(min_length=1, max_length=64)
    field: str = Field(min_length=1, max_length=48)
    value: float
    season: int | None = None
    week: int | None = Field(default=None, ge=1, le=23)
    original_value: float | None = None
    note: str = Field(default="", max_length=500)


@router.get("")
def list_overrides(
    entity_type: str | None = None,
    entity_id: str | None = None,
    season: int | None = None,
    week: int | None = None,
    db: Session = Depends(get_db),
):
    return {
        "overrides": overrides_service.list_overrides(
            db, entity_type=entity_type, entity_id=entity_id,
            season=season, week=week,
        )
    }


@router.post("")
def upsert_override(
    body: OverrideUpsert,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    try:
        return overrides_service.upsert_override(
            db,
            entity_type=body.entity_type,
            entity_id=body.entity_id,
            field=body.field,
            value=body.value,
            season=body.season,
            week=body.week,
            original_value=body.original_value,
            note=body.note,
            created_by=admin.email or "",
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None


@router.delete("/{override_id}")
def delete_override(
    override_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if not overrides_service.delete_override(db, override_id, actor=admin.email or ""):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Override not found")
    return {"deleted": override_id}


# ---- Model-input levers (baselines + current overrides for the admin UI) -----


@router.get("/model-inputs/teams")
async def team_model_inputs(season: int | None = None, db: Session = Depends(get_db)):
    """Per-team input-lever baselines (from PBP aggregates) + active overrides.

    The UI renders one row per team: baseline value, override value (if any),
    and the note. Upserts go through the generic POST with entity_type='team'.
    """
    from ..services import analytics_service, model_inputs_service
    from ..utils.seasons import current_or_upcoming_season

    season = season or current_or_upcoming_season()
    aggs = await analytics_service._team_pbp_aggregates(  # noqa: SLF001
        season, allow_live_fallback=False,
    )
    baseline_season = season
    if not aggs:
        baseline_season = season - 1
        aggs = await analytics_service._team_pbp_aggregates(  # noqa: SLF001
            baseline_season, allow_live_fallback=False,
        )
    baselines = model_inputs_service.team_input_baselines(aggs or {})
    overrides = overrides_service.team_input_overrides(db, season)
    teams = sorted(set(baselines) | set(overrides))
    return {
        "season": season,
        "baseline_season": baseline_season,
        "fields": model_inputs_service.TEAM_INPUT_LABELS,
        "teams": [
            {
                "team_id": t,
                "baselines": baselines.get(t) or {},
                "overrides": overrides.get(t) or {},
            }
            for t in teams
        ],
    }


@router.get("/model-inputs/players/{player_id}")
async def player_model_inputs(
    player_id: str, season: int | None = None, db: Session = Depends(get_db),
):
    """One player's usage-lever baselines + active overrides for the admin UI."""
    from ..models.player import Player
    from ..services import model_inputs_service
    from ..services import player_predictions_service as proj
    from ..utils.seasons import current_or_upcoming_season, latest_completed_season

    season = season or current_or_upcoming_season()
    player: Player | None = db.get(Player, player_id)
    if player is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Player not found")

    usage_season = min(season - 1, latest_completed_season())
    if season <= latest_completed_season():
        usage_season = season
    gsis = await proj._resolve_gsis(player)  # noqa: SLF001
    baselines = (await model_inputs_service.player_usage_baselines(usage_season)).get(
        gsis or "", {},
    )
    overrides = overrides_service.player_input_overrides(db, season).get(player_id, {})
    return {
        "player_id": player_id,
        "name": player.full_name,
        "position": player.position,
        "team": player.team_id,
        "season": season,
        "baseline_season": usage_season,
        "fields": model_inputs_service.PLAYER_INPUT_LABELS,
        "baselines": baselines,
        "overrides": overrides,
    }


# ---- Holistic projections board ---------------------------------------------


@router.get("/projections-board")
async def projections_board(
    season: int | None = None,
    week: int | None = None,
    scoring: str = "ppr",
    db: Session = Depends(get_db),
):
    """One row per projectable player: weekly + season projections, ranks,
    usage inputs, and every active override — the admin's holistic view.

    Composes the same cached pipelines the public boards use (weekly board +
    season leaderboard), then joins usage baselines (bulk, by gsis), active
    player overrides/input levers, and market ADP. Nothing here recomputes
    projections; it is a read-side merge, so it stays cheap and always agrees
    with what the site serves.
    """
    from ..services import model_inputs_service
    from ..services import player_predictions_service as proj
    from ..utils.seasons import current_or_upcoming_season, latest_completed_season

    season = season or current_or_upcoming_season()
    scoring = scoring if scoring in ("ppr", "half_ppr", "standard") else "ppr"

    weekly = await proj.weekly_projection_board(
        db, season=season, week=week, scoring=scoring, limit=600,
    )
    season_board = await proj.projection_leaderboard(
        db, season=season, scoring=scoring, sort="fantasy", limit=600,
    )

    usage_season = min(season - 1, latest_completed_season())
    if season <= latest_completed_season():
        usage_season = season
    try:
        baselines_by_gsis = await model_inputs_service.player_usage_baselines(usage_season)
    except Exception:  # noqa: BLE001 — baselines are enrichment, never a blocker
        baselines_by_gsis = {}

    # Every active player override, grouped: input levers vs stat/rank pins.
    all_player_ovs = overrides_service.list_overrides(db, entity_type="player")
    ovs_by_player: dict[str, list[dict]] = {}
    for o in all_player_ovs:
        ovs_by_player.setdefault(o["entity_id"], []).append(o)

    # Season rows keyed by player_id; overall + positional season ranks.
    season_rows = {r["player_id"]: r for r in season_board.get("players", [])}
    season_rank: dict[str, int] = {}
    season_pos_rank: dict[str, int] = {}
    pos_counter: dict[str, int] = {}
    for i, r in enumerate(season_board.get("players", [])):
        season_rank[r["player_id"]] = i + 1
        p = r.get("position") or "?"
        pos_counter[p] = pos_counter.get(p, 0) + 1
        season_pos_rank[r["player_id"]] = pos_counter[p]

    fantasy_key = f"fantasy_{scoring}"
    rows: list[dict] = []
    seen: set[str] = set()

    def _week_cell(wr: dict | None) -> dict:
        if not wr:
            return {}
        f = (wr.get("fantasy") or {}).get(scoring) or {}
        return {
            "week": wr.get("week"),
            "bye": bool(wr.get("bye")),
            "opponent": wr.get("opponent"),
            "is_home": wr.get("is_home"),
            "tier": wr.get("tier"),
            "pos_rank": wr.get("pos_rank"),
            "matchup_grade": wr.get("matchup_grade"),
            "defense_factor": wr.get("defense_factor"),
            "injury_multiplier": wr.get("injury_multiplier"),
            "fantasy": f.get("mean"),
            "fantasy_p10": f.get("p10"),
            "fantasy_p90": f.get("p90"),
            "stats": {
                s: (v or {}).get("mean")
                for s, v in (wr.get("predicted") or {}).items()
            },
            "game_env": wr.get("game_env"),
            "market": wr.get("market"),
        }

    def _season_cell(sr: dict | None) -> dict:
        if not sr:
            return {}
        f = sr.get(fantasy_key) or {}
        return {
            "rank": season_rank.get(sr.get("player_id")),
            "pos_rank": season_pos_rank.get(sr.get("player_id")),
            "fantasy": f.get("mean"),
            "fantasy_per_game": f.get("per_game"),
            "fantasy_p10": f.get("p10"),
            "fantasy_p90": f.get("p90"),
            "availability": sr.get("availability"),
            "games_remaining": sr.get("games_remaining"),
            "role_multiplier": (sr.get("role") or {}).get("multiplier"),
            "stats": sr.get("stats") or {},
        }

    week_rows = {r["player_id"]: r for r in weekly.get("players", [])}
    for pid in set(week_rows) | set(season_rows):
        wr, sr = week_rows.get(pid), season_rows.get(pid)
        anchor = sr or wr or {}
        gsis = (sr or {}).get("gsis_id")
        ovs = ovs_by_player.get(pid, [])
        lever_ovs = {
            o["field"]: o["value"] for o in ovs
            if o["field"] in PLAYER_INPUT_FIELDS and o["week"] is None
        }
        other_ovs = [o for o in ovs if o["field"] not in PLAYER_INPUT_FIELDS or o["week"] is not None]
        rows.append({
            "player_id": pid,
            "name": anchor.get("name"),
            "position": anchor.get("position"),
            "team": anchor.get("team"),
            "injury_status": (sr or wr or {}).get("injury_status"),
            "rookie": bool((sr or {}).get("rookie")),
            "week": _week_cell(wr),
            "season": _season_cell(sr),
            "inputs": {
                "baselines": baselines_by_gsis.get(gsis or "", {}) or {},
                "levers": lever_ovs,
            },
            "overrides": other_ovs,
            "override_count": len(ovs),
        })
        seen.add(pid)

    # Stable default order: season rank, then weekly fantasy.
    rows.sort(key=lambda r: (
        r["season"].get("rank") or 100_000,
        -(r["week"].get("fantasy") or 0.0),
    ))
    return {
        "season": season,
        "week": weekly.get("week") if isinstance(weekly, dict) else week,
        "scoring": scoring,
        "baseline_season": usage_season,
        "count": len(rows),
        "lever_fields": list(PLAYER_INPUT_FIELDS),
        "players": rows,
    }
