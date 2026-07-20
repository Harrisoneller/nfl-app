"""Custom fantasy ranking sets — CRUD, seeding, publish snapshots, public reads.

The working draft lives in ``ranking_entries``; the public board is the JSON
snapshot frozen onto the set at publish time (see models/ranking.py). All
admin writes audit through audit_service under target_type="ranking".

Design rules
------------
* Reorders are FULL-LIST replaces: the client sends the ordered board, the
  server assigns dense 1-based ranks from list position. No fractional-rank
  bookkeeping, no drift — the board you see is the board you saved.
* Tiers are validated to be 1-based and non-decreasing down the board (a
  tier break can only start a new, higher tier).
* Publishing embeds name/position/team so public reads survive roster churn;
  live injury status + model comparison are joined at read time.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..logging_config import get_logger
from ..models.player import Player
from ..models.ranking import RANKING_FORMATS, RankingEntry, RankingSet
from ..utils.seasons import current_or_upcoming_season
from . import audit_service

log = get_logger(__name__)

MAX_ENTRIES = 500

# Format tag → scoring used when comparing against the model leaderboard.
FORMAT_SCORING = {
    "ppr": "ppr", "half_ppr": "half_ppr", "standard": "standard",
    "superflex": "ppr", "two_qb": "ppr", "dynasty": "ppr",
    "best_ball": "ppr", "custom": "ppr",
}


# ---------------------------------------------------------------------------
# Set CRUD
# ---------------------------------------------------------------------------


def list_sets(db: Session, season: int | None = None) -> list[dict[str, Any]]:
    """All sets (admin view) with entry counts and a dirty flag."""
    q = db.query(RankingSet)
    if season is not None:
        q = q.filter(RankingSet.season == season)
    sets = q.order_by(RankingSet.season.desc(), RankingSet.name).all()
    counts = dict(
        db.query(RankingEntry.set_id, func.count(RankingEntry.id))
        .group_by(RankingEntry.set_id)
        .all()
    )
    out = []
    for s in sets:
        d = s.to_dict()
        d["entry_count"] = counts.get(s.id, 0)
        d["has_unpublished_changes"] = _is_dirty(s)
        out.append(d)
    return out


def create_set(
    db: Session,
    *,
    name: str,
    season: int | None = None,
    format: str = "custom",
    description: str = "",
    created_by: str = "",
) -> RankingSet:
    name = (name or "").strip()
    if not name:
        raise ValueError("Ranking set name is required")
    if format not in RANKING_FORMATS:
        raise ValueError(f"Unknown format '{format}' (use one of {RANKING_FORMATS})")
    season = season or current_or_upcoming_season()
    exists = (
        db.query(RankingSet.id)
        .filter(RankingSet.season == season, RankingSet.name == name)
        .first()
    )
    if exists:
        raise ValueError(f"A ranking set named '{name}' already exists for {season}")
    s = RankingSet(
        name=name, season=season, format=format,
        description=description or "", created_by=created_by,
    )
    db.add(s)
    audit_service.record(
        db, actor=created_by, action="ranking_create", target_type="ranking",
        target_key=f"{season}:{name}", note=description,
        context={"format": format},
    )
    db.commit()
    db.refresh(s)
    return s


def update_set(
    db: Session,
    set_id: int,
    *,
    name: str | None = None,
    format: str | None = None,
    description: str | None = None,
    actor: str = "",
) -> RankingSet | None:
    s = db.get(RankingSet, set_id)
    if s is None:
        return None
    if name is not None and name.strip() and name.strip() != s.name:
        clash = (
            db.query(RankingSet.id)
            .filter(
                RankingSet.season == s.season,
                RankingSet.name == name.strip(),
                RankingSet.id != s.id,
            )
            .first()
        )
        if clash:
            raise ValueError(f"A ranking set named '{name.strip()}' already exists for {s.season}")
        s.name = name.strip()
    if format is not None:
        if format not in RANKING_FORMATS:
            raise ValueError(f"Unknown format '{format}'")
        s.format = format
    if description is not None:
        s.description = description
    audit_service.record(
        db, actor=actor, action="ranking_update", target_type="ranking",
        target_key=f"{s.season}:{s.name}",
    )
    db.commit()
    db.refresh(s)
    return s


def delete_set(db: Session, set_id: int, *, actor: str = "") -> bool:
    s = db.get(RankingSet, set_id)
    if s is None:
        return False
    audit_service.record(
        db, actor=actor, action="ranking_delete", target_type="ranking",
        target_key=f"{s.season}:{s.name}",
        context={"entries": len(s.entries), "version": s.version},
    )
    db.delete(s)
    db.commit()
    return True


# ---------------------------------------------------------------------------
# Draft entries (the working board)
# ---------------------------------------------------------------------------


def get_set_detail(db: Session, set_id: int) -> dict[str, Any] | None:
    """Set meta + draft entries enriched with player info (admin editor view)."""
    s = db.get(RankingSet, set_id)
    if s is None:
        return None
    d = s.to_dict()
    d["entry_count"] = len(s.entries)
    d["has_unpublished_changes"] = _is_dirty(s)
    d["entries"] = _enrich(db, [e.to_dict() for e in s.entries])
    return d


def replace_entries(
    db: Session,
    set_id: int,
    entries: list[dict[str, Any]],
    *,
    actor: str = "",
) -> dict[str, Any]:
    """Full-board replace: list order IS the ranking. Each item:
    {player_id, tier?, note?}. Ranks are assigned server-side (1-based)."""
    s = db.get(RankingSet, set_id)
    if s is None:
        raise LookupError("Ranking set not found")
    if len(entries) > MAX_ENTRIES:
        raise ValueError(f"Too many entries (max {MAX_ENTRIES})")

    seen: set[str] = set()
    cleaned: list[tuple[str, int, str]] = []
    prev_tier = 1
    for i, e in enumerate(entries):
        pid = str(e.get("player_id") or "").strip()
        if not pid:
            raise ValueError(f"Entry {i + 1}: player_id is required")
        if pid in seen:
            raise ValueError(f"Duplicate player in board: {pid}")
        seen.add(pid)
        tier = int(e.get("tier") or prev_tier)
        if tier < prev_tier:
            raise ValueError(
                f"Entry {i + 1}: tier {tier} decreases down the board (previous {prev_tier})"
            )
        prev_tier = tier
        cleaned.append((pid, tier, str(e.get("note") or "")[:200]))

    # Validate players exist (one query).
    if cleaned:
        ids = [c[0] for c in cleaned]
        found = {p for (p,) in db.query(Player.id).filter(Player.id.in_(ids)).all()}
        missing = [p for p in ids if p not in found]
        if missing:
            raise ValueError(f"Unknown player ids: {', '.join(missing[:5])}")

    db.query(RankingEntry).filter(RankingEntry.set_id == set_id).delete()
    for rank, (pid, tier, note) in enumerate(cleaned, start=1):
        db.add(RankingEntry(set_id=set_id, player_id=pid, rank=rank, tier=tier, note=note))
    audit_service.record(
        db, actor=actor, action="ranking_edit", target_type="ranking",
        target_key=f"{s.season}:{s.name}", new_value=float(len(cleaned)),
        context={"entries": len(cleaned)},
    )
    db.commit()
    return get_set_detail(db, set_id)  # type: ignore[return-value]


async def seed_from_projections(
    db: Session,
    set_id: int,
    *,
    source: str = "ros_vorp",
    scoring: str | None = None,
    position: str | None = None,
    limit: int = 200,
    actor: str = "",
) -> dict[str, Any]:
    """Populate the draft from the model — a starting point to reshape.

    source="ros_vorp"     → fantasy_insights ROS value board (VORP order,
                            tiers carried over).
    source="season_total" → season projection leaderboard (total points
                            order, single tier).
    Replaces existing entries.
    """
    s = db.get(RankingSet, set_id)
    if s is None:
        raise LookupError("Ranking set not found")
    scoring = scoring or FORMAT_SCORING.get(s.format, "ppr")
    limit = max(1, min(limit, MAX_ENTRIES))

    rows: list[dict[str, Any]] = []
    if source == "season_total":
        from . import player_predictions_service as proj

        board = await proj.projection_leaderboard(
            db, season=s.season, position=position, scoring=scoring,
            sort="fantasy", limit=limit,
        )
        rows = [
            {"player_id": r["player_id"], "tier": 1}
            for r in board.get("players", []) if r.get("player_id")
        ]
    else:  # ros_vorp
        from . import fantasy_insights_service

        board = await fantasy_insights_service.ros_value_board(
            db, season=s.season, scoring=scoring, position=position, limit=limit,
        )
        rows = [
            {"player_id": r["player_id"], "tier": int(r.get("tier") or 1)}
            for r in board.get("players", []) if r.get("player_id")
        ]
        # ros tiers are per-position; when seeding ALL positions make tiers
        # non-decreasing in overall order so board validation holds.
        prev = 1
        for r in rows:
            r["tier"] = prev = max(prev, r["tier"]) if position is None else r["tier"]

    if not rows:
        raise ValueError("Projection board returned no players to seed from")

    audit_service.record(
        db, actor=actor, action="ranking_seed", target_type="ranking",
        target_key=f"{s.season}:{s.name}", new_value=float(len(rows)),
        context={"source": source, "scoring": scoring, "position": position},
    )
    return replace_entries(db, set_id, rows, actor=actor)


# ---------------------------------------------------------------------------
# Publish / unpublish
# ---------------------------------------------------------------------------


def publish(db: Session, set_id: int, *, actor: str = "") -> dict[str, Any]:
    """Freeze the current draft into the public snapshot."""
    s = db.get(RankingSet, set_id)
    if s is None:
        raise LookupError("Ranking set not found")
    if not s.entries:
        raise ValueError("Cannot publish an empty board")

    snapshot = _enrich(db, [e.to_dict() for e in s.entries])
    s.published_json = json.dumps(
        [
            {
                "player_id": r["player_id"], "rank": r["rank"], "tier": r["tier"],
                "note": r["note"], "name": r.get("name"),
                "position": r.get("position"), "team": r.get("team"),
            }
            for r in snapshot
        ]
    )
    s.version += 1
    s.status = "published"
    s.published_at = func.now()
    audit_service.record(
        db, actor=actor, action="ranking_publish", target_type="ranking",
        target_key=f"{s.season}:{s.name}", new_value=float(s.version),
        context={"entries": len(snapshot)},
    )
    db.commit()
    db.refresh(s)
    d = s.to_dict()
    d["entry_count"] = len(s.entries)
    d["has_unpublished_changes"] = False
    return d


def unpublish(db: Session, set_id: int, *, actor: str = "") -> dict[str, Any]:
    """Pull the board off the public page (draft + snapshot both kept)."""
    s = db.get(RankingSet, set_id)
    if s is None:
        raise LookupError("Ranking set not found")
    s.status = "draft"
    audit_service.record(
        db, actor=actor, action="ranking_unpublish", target_type="ranking",
        target_key=f"{s.season}:{s.name}",
    )
    db.commit()
    db.refresh(s)
    d = s.to_dict()
    d["entry_count"] = len(s.entries)
    d["has_unpublished_changes"] = _is_dirty(s)
    return d


# ---------------------------------------------------------------------------
# Public reads (fantasy page)
# ---------------------------------------------------------------------------


def public_sets(db: Session, season: int | None = None) -> list[dict[str, Any]]:
    season = season or current_or_upcoming_season()
    sets = (
        db.query(RankingSet)
        .filter(
            RankingSet.season == season,
            RankingSet.status == "published",
            RankingSet.published_json.isnot(None),
        )
        .order_by(RankingSet.name)
        .all()
    )
    return [
        {
            "id": s.id, "name": s.name, "season": s.season, "format": s.format,
            "description": s.description, "version": s.version,
            "published_at": s.published_at.isoformat() if s.published_at else None,
        }
        for s in sets
    ]


async def public_rankings(db: Session, set_id: int) -> dict[str, Any] | None:
    """The published snapshot + live injury status + model-rank comparison."""
    s = db.get(RankingSet, set_id)
    if s is None or s.status != "published" or not s.published_json:
        return None
    try:
        rows: list[dict[str, Any]] = json.loads(s.published_json)
    except ValueError:
        log.warning("ranking set %s has corrupt snapshot json", set_id)
        return None

    # Live injury status (roster table, one query).
    ids = [r["player_id"] for r in rows]
    players = {p.id: p for p in db.query(Player).filter(Player.id.in_(ids)).all()}
    for r in rows:
        p = players.get(r["player_id"])
        r["injury_status"] = (p.metadata_json or {}).get("injury_status") if p else None
        # Prefer live team (trades) but keep snapshot as fallback.
        if p is not None and p.team_id:
            r["team"] = p.team_id

    # Model comparison — enrichment only, never a blocker.
    scoring = FORMAT_SCORING.get(s.format, "ppr")
    try:
        from . import player_predictions_service as proj

        board = await proj.projection_leaderboard(
            db, season=s.season, scoring=scoring, sort="fantasy", limit=600,
        )
        model_rank = {
            r["player_id"]: i + 1
            for i, r in enumerate(board.get("players", []))
        }
        fkey = f"fantasy_{scoring}"
        model_pts = {
            r["player_id"]: (r.get(fkey) or {}).get("mean")
            for r in board.get("players", [])
        }
        for r in rows:
            mr = model_rank.get(r["player_id"])
            r["model_rank"] = mr
            r["vs_model"] = (mr - r["rank"]) if mr is not None else None
            r["model_points"] = model_pts.get(r["player_id"])
    except Exception:  # noqa: BLE001
        log.warning("model comparison unavailable for ranking set %s", set_id, exc_info=True)
        for r in rows:
            r.setdefault("model_rank", None)
            r.setdefault("vs_model", None)
            r.setdefault("model_points", None)

    return {
        "id": s.id, "name": s.name, "season": s.season, "format": s.format,
        "description": s.description, "version": s.version,
        "published_at": s.published_at.isoformat() if s.published_at else None,
        "scoring_for_comparison": scoring,
        "count": len(rows),
        "players": rows,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enrich(db: Session, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join player name/position/team/injury onto raw entry dicts."""
    if not entries:
        return []
    ids = [e["player_id"] for e in entries]
    players = {p.id: p for p in db.query(Player).filter(Player.id.in_(ids)).all()}
    for e in entries:
        p = players.get(e["player_id"])
        e["name"] = p.full_name if p else e.get("name")
        e["position"] = p.position if p else e.get("position")
        e["team"] = p.team_id if p else e.get("team")
        e["injury_status"] = (p.metadata_json or {}).get("injury_status") if p else None
    return entries


def _is_dirty(s: RankingSet) -> bool:
    """True when the draft differs from the published snapshot."""
    if s.published_json is None:
        return bool(s.entries)
    try:
        snap = [
            (r["player_id"], r["rank"], r["tier"], r.get("note", ""))
            for r in json.loads(s.published_json)
        ]
    except ValueError:
        return True
    cur = [(e.player_id, e.rank, e.tier, e.note) for e in s.entries]
    return snap != cur
