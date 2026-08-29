"""Context layer — reduce append-only snapshots to a per-game adjustment.

This is the read side of ``models/team_context.py``. Providers (availability,
QB, staff change, scheme matchup, situational, weather) write ``value_pts`` /
``sigma_mult`` rows into ``team_context_snapshots``; this module resolves them
into one bundle per slate and one adjustment per game, and ``predict_game``
applies it.

Three properties matter more than anything clever this module could do:

1. **``as_of`` is a first-class argument.** Every read can be pinned to a
   timestamp. A backtest that reads "current" context leaks the future into
   every historical game and will report an edge that does not exist. The
   default (``None`` = now) is the live path; the harness passes the time the
   line was available.

2. **Components stay separate all the way out.** The bundle keeps each
   component's own points, confidence and detail so the explainability panel
   can say "-2.6 availability, -0.4 short week" rather than "-3.0 context". A
   number a reader cannot decompose is a number nobody can audit — us included.

3. **Everything is capped and fails open.** Context refines a prediction; it
   must never be able to prevent one, and no single provider may dominate a
   line. A bad injury feed should cost us a fraction of a point, not a game.

Nothing here computes context — providers do that, and they land in
``services/context/`` in Phase 2. Until then the table is fed by the admin
manual path, which works today.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..logging_config import get_logger
from ..models.team_context import (
    CONTEXT_COMPONENTS,
    SOURCE_PRIORITY,
    TeamContextSnapshot,
)
from ..utils.teams import canonical_team

log = get_logger(__name__)

# Components that apply only to a specific matchup rather than to the team
# league-wide. These are keyed by (team, opponent) and must never be summed
# into a team's standing context.
_MATCHUP_COMPONENTS = frozenset({"scheme_matchup"})


def _p(key: str, default: float) -> float:
    try:
        from . import param_registry
        return float(param_registry.value(key))
    except Exception:  # noqa: BLE001 — must run without a DB / registry
        return default


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def enabled() -> bool:
    """Master switch. Off = predictions fall back to the pure historical model."""
    return _p("context.enabled", 1.0) >= 0.5


def empty_bundle(season: int | None = None, week: int | None = None) -> dict[str, Any]:
    return {
        "season": season,
        "week": week,
        "teams": {},
        "matchup": {},
        "as_of": None,
        "applicable": False,
    }


# ---- Building the slate bundle ---------------------------------------------


def week_context(
    db: Session,
    season: int,
    week: int | None,
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Resolve every team's context for one slate into a single bundle.

    Reads the append-only snapshot table, keeps the newest row per
    (team, opponent, component, source) at or before ``as_of``, resolves
    competing sources by SOURCE_PRIORITY, and sums the surviving components.

    Best-effort by contract: any failure returns an empty bundle and the model
    runs exactly as it did before the context layer existed.
    """
    if not enabled():
        return empty_bundle(season, week)

    try:
        stmt = select(TeamContextSnapshot).where(
            TeamContextSnapshot.season == season,
        )
        if week is not None:
            # Week-scoped rows plus season-scoped ones (week IS NULL), which is
            # how a season-ending injury or an offseason staff change is stored.
            stmt = stmt.where(
                (TeamContextSnapshot.week == week)
                | (TeamContextSnapshot.week.is_(None))
            )
        if as_of is not None:
            stmt = stmt.where(TeamContextSnapshot.captured_at <= as_of)
        rows = db.execute(stmt).scalars().all()
    except Exception as e:  # noqa: BLE001 — context must never take predictions down
        log.warning("context_week_read_failed", season=season, week=week,
                    error=str(e)[:200])
        return empty_bundle(season, week)

    if not rows:
        return empty_bundle(season, week)

    # Newest row wins per (team, opponent, component, source); then the
    # highest-priority source wins per (team, opponent, component).
    newest: dict[tuple, TeamContextSnapshot] = {}
    for r in rows:
        team = canonical_team(r.team_id) or r.team_id
        opp = canonical_team(r.opponent_id) or r.opponent_id if r.opponent_id else None
        key = (team, opp, r.component, r.source)
        prev = newest.get(key)
        if prev is None or (_aware(r.captured_at) or _EPOCH) > (
                _aware(prev.captured_at) or _EPOCH):
            newest[key] = r

    resolved: dict[tuple, TeamContextSnapshot] = {}
    for (team, opp, component, source), r in newest.items():
        key = (team, opp, component)
        prev = resolved.get(key)
        if prev is None or _priority(source) > _priority(prev.source):
            resolved[key] = r

    cutoff = _staleness_cutoff(as_of)
    min_conf = _p("context.min_confidence", 0.25)

    teams: dict[str, dict[str, Any]] = {}
    matchup: dict[str, list[dict[str, Any]]] = {}

    for (team, opp, component), r in resolved.items():
        if component not in CONTEXT_COMPONENTS:
            continue
        # Dropped, not discounted: keeping a fraction of a signal we do not
        # believe is how a model drifts a little further every week.
        if float(r.confidence or 0.0) < min_conf:
            continue
        captured = _aware(r.captured_at)
        if captured and cutoff and captured < cutoff:
            continue

        entry = {
            "component": component,
            "points": float(r.value_pts or 0.0),
            "sigma_mult": float(r.sigma_mult or 1.0),
            "confidence": float(r.confidence or 1.0),
            "source": r.source,
            "detail": r.detail or {},
            "evidence_url": r.evidence_url,
            "captured_at": captured.isoformat() if captured else None,
        }

        if component in _MATCHUP_COMPONENTS and opp:
            matchup.setdefault(f"{team}|{opp}", []).append(entry)
            continue

        slot = teams.setdefault(team, {
            "team_id": team,
            "points": 0.0,
            "sigma_mult": 1.0,
            "components": [],
        })
        slot["points"] += entry["points"]
        slot["sigma_mult"] *= entry["sigma_mult"]
        slot["components"].append(entry)

    for slot in teams.values():
        comps = slot["components"]
        slot["points"] = round(slot["points"], 3)
        slot["sigma_mult"] = round(slot["sigma_mult"], 4)
        slot["confidence"] = round(
            sum(c["confidence"] for c in comps) / len(comps), 3) if comps else 0.0

    return {
        "season": season,
        "week": week,
        "teams": teams,
        "matchup": matchup,
        "as_of": as_of.isoformat() if as_of else None,
        "applicable": bool(teams or matchup),
    }


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    """Normalize a timestamp to UTC-aware.

    Postgres hands back aware datetimes for ``DateTime(timezone=True)``, but
    SQLite (the test fixture) and some drivers hand back naive ones. Comparing
    the two raises, so every capture time is normalized on the way in rather
    than trusting the driver.
    """
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _priority(source: str | None) -> int:
    return SOURCE_PRIORITY.get(source or "derived", 0)


def _staleness_cutoff(as_of: datetime | None) -> datetime | None:
    hours = _p("context.staleness_hours", 72.0)
    if hours <= 0:
        return None
    ref = as_of or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return ref - timedelta(hours=hours)


# ---- Reducing the bundle to one game ---------------------------------------


def game_context(
    bundle: dict[str, Any] | None,
    home_team: str | None,
    away_team: str | None,
) -> dict[str, Any]:
    """Home-perspective context for one matchup.

    ``points`` is signed like a margin from the home side: positive means the
    context favors the home team. The per-side payloads are preserved untouched
    so the explainability panel can show each team's own story rather than only
    the net — "their QB is out" and "our line is banged up" are different
    sentences even when they sum to zero.
    """
    empty = {
        "points": 0.0, "sigma_mult": 1.0, "applicable": False,
        "home": None, "away": None, "matchup": None, "confidence": 0.0,
    }
    if not bundle or not bundle.get("applicable"):
        return empty

    teams = bundle.get("teams") or {}
    h_key = canonical_team(home_team) or home_team or ""
    a_key = canonical_team(away_team) or away_team or ""
    h = teams.get(h_key)
    a = teams.get(a_key)

    points = float((h or {}).get("points") or 0.0) - float((a or {}).get("points") or 0.0)
    # Uncertainty compounds regardless of which side it sits on: a game with a
    # questionable QB on either roster is a wider game.
    sigma = float((h or {}).get("sigma_mult") or 1.0) * float((a or {}).get("sigma_mult") or 1.0)

    mk = (bundle.get("matchup") or {})
    pair = list(mk.get(f"{h_key}|{a_key}") or [])
    away_pair = mk.get(f"{a_key}|{h_key}") or []
    for entry in pair:
        points += float(entry.get("points") or 0.0)
        sigma *= float(entry.get("sigma_mult") or 1.0)
    for entry in away_pair:
        # Stored from the away team's perspective — flip into home terms.
        points -= float(entry.get("points") or 0.0)
        sigma *= float(entry.get("sigma_mult") or 1.0)

    cap = _p("context.game_cap_pts", 7.0)
    sigma_cap = _p("context.sigma_mult_cap", 1.25)
    raw = points
    points = _clamp(points, -cap, cap)

    confidences = [c.get("confidence") for c in (h, a) if c]
    return {
        "points": round(points, 2),
        "points_uncapped": round(raw, 2),
        "capped": abs(raw) > cap + 1e-9,
        "sigma_mult": round(min(sigma_cap, max(1.0, sigma)), 4),
        "applicable": bool(h or a or pair or away_pair),
        "confidence": round(
            sum(float(c or 0.0) for c in confidences) / len(confidences), 3
        ) if confidences else 0.0,
        "home": h,
        "away": a,
        "matchup": (pair + list(away_pair)) or None,
    }


def context_points(bundle: dict[str, Any] | None, team_id: str | None) -> float:
    """A single team's week-level context points (for the season simulator)."""
    if not bundle or not team_id:
        return 0.0
    entry = (bundle.get("teams") or {}).get(canonical_team(team_id) or team_id)
    return float((entry or {}).get("points") or 0.0)


# ---- Write side -------------------------------------------------------------


def persist(
    db: Session,
    *,
    season: int,
    week: int | None,
    rows: list[dict[str, Any]],
    model_version: str | None = None,
    captured_at: datetime | None = None,
) -> int:
    """Append component rows. Never updates: a new observation is a new row.

    ``rows`` entries need ``team_id`` and ``component``; everything else has a
    sane default. Returns the number of rows written.
    """
    if not rows:
        return 0
    ts = captured_at or datetime.now(timezone.utc)
    written = 0
    for r in rows:
        component = r.get("component")
        team_id = canonical_team(r.get("team_id")) or r.get("team_id")
        if not team_id or component not in CONTEXT_COMPONENTS:
            continue
        db.add(TeamContextSnapshot(
            season=season,
            week=week,
            team_id=team_id,
            opponent_id=canonical_team(r.get("opponent_id")) or r.get("opponent_id"),
            component=component,
            source=r.get("source") or "derived",
            value_pts=float(r.get("points") or r.get("value_pts") or 0.0),
            sigma_mult=float(r.get("sigma_mult") or 1.0),
            confidence=float(r.get("confidence") if r.get("confidence") is not None else 1.0),
            detail=r.get("detail") or {},
            evidence_url=r.get("evidence_url"),
            model_version=model_version,
            captured_at=ts,
        ))
        written += 1
    if written:
        db.commit()
    return written


async def refresh_context(
    db: Session,
    season: int,
    week: int | None,
    *,
    teams: list[str] | None = None,
) -> dict[str, Any]:
    """Run every provider for one slate and append what they produce.

    This is the scheduler entry point. Each provider is isolated: one failing
    feed costs its own component, never the others and never the predictions.
    Returns a per-provider report so the admin panel can show what actually
    ran rather than only what was supposed to.
    """
    report: dict[str, Any] = {"season": season, "week": week, "providers": {}}
    if not enabled():
        report["skipped"] = "context.enabled is off"
        return report

    # 1. Ingest the official injury report (append-only).
    try:
        from .context import availability

        ingested = await availability.refresh_availability(db, season, week)
        report["providers"]["availability_ingest"] = {"rows": ingested}
    except Exception as e:  # noqa: BLE001
        log.warning("context_refresh_ingest_failed", error=str(e)[:200])
        report["providers"]["availability_ingest"] = {"error": str(e)[:200]}

    # 2. Price it into components (availability + qb).
    try:
        from .context import availability

        rows = await availability.availability_context(
            db, season, week, teams=teams)
        written = persist(db, season=season, week=week, rows=rows,
                          model_version=availability.AVAILABILITY_MODEL_VERSION)
        report["providers"]["availability_price"] = {
            "components": len(rows), "written": written,
        }
    except Exception as e:  # noqa: BLE001
        log.warning("context_refresh_price_failed", error=str(e)[:200])
        report["providers"]["availability_price"] = {"error": str(e)[:200]}

    return report


def record_manual(
    db: Session,
    *,
    season: int,
    week: int | None,
    team_id: str,
    points: float,
    note: str,
    sigma_mult: float = 1.0,
    opponent_id: str | None = None,
    evidence_url: str | None = None,
) -> int:
    """Admin-entered context. A note is required — an unexplained manual
    adjustment is indistinguishable from a bug six weeks later.
    """
    if not note or not note.strip():
        raise ValueError("a manual context entry requires a note")
    return persist(
        db,
        season=season,
        week=week,
        rows=[{
            "team_id": team_id,
            "opponent_id": opponent_id,
            "component": "manual",
            "source": "admin",
            "points": float(points),
            "sigma_mult": float(sigma_mult),
            "confidence": 1.0,
            "detail": {"note": note.strip()},
            "evidence_url": evidence_url,
        }],
    )
