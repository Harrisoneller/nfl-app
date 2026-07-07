"""Player CRUD + Sleeper sync."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..adapters.data.sleeper import SleeperAdapter
from ..logging_config import get_logger
from ..models.player import Player

log = get_logger(__name__)

# Sleeper team abbreviation → our team_id (mostly identical).
TEAM_MAP = {
    "JAC": "JAX", "WSH": "WAS", "ARZ": "ARI", "LA": "LAR", "OAK": "LV", "SD": "LAC",
}


def list_players(
    db: Session, *, position: str | None = None, team_id: str | None = None,
    search: str | None = None, limit: int = 100, offset: int = 0,
) -> list[Player]:
    q = db.query(Player)
    if position:
        q = q.filter(Player.position == position.upper())
    if team_id:
        q = q.filter(Player.team_id == team_id.upper())
    if search:
        like = f"%{search}%"
        q = q.filter(Player.full_name.ilike(like))
    return q.order_by(Player.full_name).offset(offset).limit(limit).all()


def get_player(db: Session, player_id: str) -> Player | None:
    return db.get(Player, player_id)


def get_team_roster(db: Session, team_id: str) -> list[Player]:
    return (
        db.query(Player)
        .filter(Player.team_id == team_id.upper(), Player.status == "Active")
        .order_by(Player.position, Player.full_name)
        .all()
    )


async def sync_from_sleeper(db: Session, active_only: bool = True) -> int:
    """Pull NFL players from Sleeper and upsert.

    Sleeper's `/players/nfl` returns ~11,400 records including every player
    who's ever been on a roster. With `active_only=True` (default) we filter
    to those currently on a team — usually ~2,000 — which makes the sync
    ~5x faster and avoids polluting search results with retired players.
    """
    adapter = SleeperAdapter()
    try:
        all_players = await adapter.fetch_all_players()
    finally:
        await adapter.aclose()

    n = 0
    for pid, raw in all_players.items():
        if not raw or raw.get("position") is None:
            continue
        team_raw = raw.get("team")
        if active_only and not team_raw:
            continue
        team_id = TEAM_MAP.get(team_raw, team_raw) if team_raw else None

        p = db.get(Player, pid)
        if p is None:
            p = Player(id=pid)
            db.add(p)
        # Sleeper's gsis_id is unreliable: null for many players and padded
        # with a leading space when present (" 00-0034796"). Normalize here so
        # downstream joins against nflverse GSIS ids actually match; never
        # overwrite a previously backfilled id with a null.
        clean_gsis = normalize_gsis_id(raw.get("gsis_id"))
        if clean_gsis:
            p.gsis_id = clean_gsis
        p.espn_id = _safe_int(raw.get("espn_id"))
        p.full_name = (raw.get("full_name") or
                       f"{raw.get('first_name','')} {raw.get('last_name','')}".strip())
        p.position = (raw.get("position") or "")[:8]
        p.team_id = team_id
        p.jersey_number = _safe_int(raw.get("number"))
        p.age = _safe_int(raw.get("age"))
        p.height = (raw.get("height") or None)
        p.weight = _safe_int(raw.get("weight"))
        p.college = (raw.get("college") or None)
        p.status = (raw.get("status") or "")[:32]
        p.metadata_json = {
            "fantasy_positions": raw.get("fantasy_positions"),
            "depth_chart_position": raw.get("depth_chart_position"),
            "depth_chart_order": raw.get("depth_chart_order"),
            "injury_status": raw.get("injury_status"),
            "injury_body_part": raw.get("injury_body_part"),
            "injury_notes": raw.get("injury_notes"),
            "years_exp": raw.get("years_exp"),
        }
        n += 1
    db.commit()
    log.info("players_synced", count=n)

    # Best-effort: repair missing gsis ids from the nflverse crosswalk so the
    # projection layer (which joins on GSIS) sees every rostered player.
    try:
        filled = await backfill_gsis_ids(db)
        if filled:
            log.info("players_gsis_backfilled", count=filled)
    except Exception as e:  # noqa: BLE001
        log.warning("players_gsis_backfill_failed", error=str(e)[:200])
    return n


def normalize_gsis_id(v) -> str | None:
    """'  00-0034796 ' → '00-0034796'; ''/None/non-str → None."""
    if not isinstance(v, str):
        return None
    v = v.strip()
    return v or None


async def backfill_gsis_ids(db: Session) -> int:
    """Fill missing/blank Player.gsis_id from the nflverse id crosswalk.

    Player.id IS the Sleeper id, and nflverse's ids frame carries a
    sleeper_id ↔ gsis_id mapping — an exact join, no name matching needed.
    Also repairs previously stored whitespace-padded ids in place.
    """
    from .player_predictions_service import _nfl  # shared adapter instance

    ids = await _nfl.ids_df()
    if ids is None or len(ids) == 0:
        return 0
    cols = {c.lower(): c for c in ids.columns}
    scol, gcol = cols.get("sleeper_id"), cols.get("gsis_id")
    if not scol or not gcol:
        return 0
    sub = ids[[scol, gcol]].dropna()
    by_sleeper: dict[str, str] = {}
    for s, g in zip(sub[scol], sub[gcol]):
        g = normalize_gsis_id(str(g))
        if g is None:
            continue
        # sleeper_id arrives as float ("4046.0") in some releases — canonicalize.
        s = str(s)
        if s.endswith(".0"):
            s = s[:-2]
        by_sleeper[s] = g

    n = 0
    for p in db.query(Player).all():
        clean = normalize_gsis_id(p.gsis_id)
        mapped = by_sleeper.get(str(p.id))
        target = clean or mapped
        if target and p.gsis_id != target:
            p.gsis_id = target
            n += 1
    if n:
        db.commit()
    return n


def _safe_int(v) -> int | None:
    try:
        return int(v) if v not in (None, "", "null") else None
    except (TypeError, ValueError):
        return None
