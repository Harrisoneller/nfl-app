"""Who is not playing, and what it costs.

Pipeline
--------
``refresh_availability`` ingests the official NFL injury report into
``player_availability_snapshots`` (append-only). ``resolve_availability`` reads
the newest row per player per source and applies SOURCE_PRIORITY.
``availability_context`` prices the resolved picture into points and a sigma
multiplier per team, which ``context_service`` hands to ``predict_game``.

The pricing identity
--------------------
    points = Σ over players  (1 − play_probability) × value_above_replacement

Quarterbacks are excluded from that sum and priced separately through
``qb_adjustment_service.starter_swing`` — the backup is a specific person, not
a positional average, and the swing dwarfs everything else.

Two judgments worth stating plainly
-----------------------------------
**Unreported is not healthy.** A team with no rows this week produces zero
points *and* zero confidence, plus a small sigma widening. The difference
between "we know they are healthy" and "we have not heard" is exactly the
difference between a number you can bet and a number you cannot, and collapsing
the two is how a context layer quietly starts lying.

**Practice participation beats the designation.** The game-status tag
(Out/Doubtful/Questionable) is strategically reported and heavily bunched on
Questionable. The Wed/Thu/Fri participation sequence is the real signal: DNP →
DNP → DNP is a different player from LP → LP → FP even when both are listed
Questionable. ``play_probability`` keys on the trajectory when it is available
and falls back to the tag when it is not.

The status→probability numbers are priors awaiting a fit
--------------------------------------------------------
``DEFAULT_PLAY_PROB`` and ``_TRAJECTORY_ADJUST`` are considered starting values,
not measurements. ``fit_play_probability`` measures the real mapping from our
own accumulated snapshots joined to realized snap counts. Until it has run on a
real sample, every number this module produces carries that caveat, and the
CLV validation gate is what should decide whether the layer is trusted at all.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...logging_config import get_logger
from ...models.team_context import (
    DEFAULT_PLAY_PROB,
    SOURCE_PRIORITY,
    PlayerAvailability,
)
from ...utils.teams import canonical_team
from . import player_value

log = get_logger(__name__)

AVAILABILITY_MODEL_VERSION = "avail-nfl-v1"

# Normalization of the many spellings the feeds use for the same state.
_STATUS_ALIASES: dict[str, str] = {
    "out": "out", "o": "out", "inactive": "out",
    "doubtful": "doubtful", "d": "doubtful",
    "questionable": "questionable", "q": "questionable",
    "injured reserve": "ir", "ir": "ir", "reserve/injured": "ir",
    "pup": "pup", "physically unable to perform": "pup",
    "nfi": "nfi", "non football injury": "nfi",
    "suspended": "suspended", "reserve/suspended": "suspended",
    "active": "available", "available": "available", "probable": "available",
    "full": "available",
}

_PRACTICE_ALIASES: dict[str, str] = {
    "dnp": "dnp", "did not participate in practice": "dnp",
    "did not participate": "dnp", "out": "dnp",
    "limited": "limited", "limited participation in practice": "limited",
    "lp": "limited",
    "full": "full", "full participation in practice": "full", "fp": "full",
}

# Multiplicative adjustments to the status prior, keyed by the Wed/Thu/Fri
# participation trajectory. A player trending up through the week plays far
# more often than the shared "Questionable" tag suggests; one trending down or
# never practising plays far less. Priors, awaiting the same fit as the rest.
_TRAJECTORY_ADJUST: dict[str, float] = {
    "improving": 1.25,     # e.g. DNP -> LP -> FP
    "full_by_friday": 1.35,
    "steady_limited": 1.00,
    "declining": 0.55,     # practised early, stopped
    "never_practised": 0.30,
}


def _p(key: str, default: float) -> float:
    try:
        from .. import param_registry
        return float(param_registry.value(key))
    except Exception:  # noqa: BLE001
        return default


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def normalize_status(raw: Any) -> str:
    if raw is None:
        return "unknown"
    s = str(raw).strip().lower()
    if not s or s in ("nan", "none", "--"):
        return "unknown"
    return _STATUS_ALIASES.get(s, "unknown")


def normalize_practice(raw: Any) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s or s in ("nan", "none", "--"):
        return None
    return _PRACTICE_ALIASES.get(s)


def trajectory(wed: str | None, thu: str | None, fri: str | None) -> str | None:
    """Classify the practice week. None when we have no participation data."""
    seq = [d for d in (wed, thu, fri) if d]
    if not seq:
        return None
    rank = {"dnp": 0, "limited": 1, "full": 2}
    vals = [rank.get(d, 1) for d in seq]
    if all(v == 0 for v in vals):
        return "never_practised"
    if seq[-1] == "full":
        return "full_by_friday"
    if len(vals) >= 2 and vals[-1] > vals[0]:
        return "improving"
    if len(vals) >= 2 and vals[-1] < vals[0]:
        return "declining"
    return "steady_limited"


def play_probability(
    status: str,
    *,
    wed: str | None = None,
    thu: str | None = None,
    fri: str | None = None,
) -> float:
    """P(this player takes the field), from the designation and the practice week.

    Statuses that are definitionally absences (Out, IR, PUP, NFI, suspended)
    ignore the practice signal entirely — a player on IR did not practise
    because he is on IR, and letting the trajectory nudge that toward 'might
    play' would be nonsense.
    """
    base = _p(f"context.play_prob_{status}", DEFAULT_PLAY_PROB.get(status, 0.9))
    if base <= 0.0:
        return 0.0

    traj = trajectory(wed, thu, fri)
    if traj is None:
        return _clamp01(base)

    adjusted = base * _TRAJECTORY_ADJUST.get(traj, 1.0)
    # A player who never practised is very unlikely to play regardless of how
    # optimistic the designation reads.
    if traj == "never_practised":
        adjusted = min(adjusted, 0.35)
    return _clamp01(adjusted)


# ---- Ingest -----------------------------------------------------------------


def _col(df: pd.DataFrame, *names: str) -> str | None:
    for n in names:
        if n in df.columns:
            return n
    return None


async def refresh_availability(
    db: Session,
    season: int,
    week: int | None = None,
) -> int:
    """Pull the official injury report and append it. Returns rows written.

    Append-only: a re-run mid-week adds a new observation rather than editing
    the old one, which is what lets the backtest replay Wednesday's picture.
    """
    try:
        from ...adapters.data.nfl_data_py_adapter import NflDataPyAdapter

        df = await NflDataPyAdapter().injuries_df(season)
    except Exception as e:  # noqa: BLE001
        log.warning("availability_fetch_failed", season=season, error=str(e)[:200])
        return 0

    if df is None or len(df) == 0:
        log.info("availability_feed_empty", season=season)
        return 0

    week_col = _col(df, "week")
    team_col = _col(df, "team", "club_code", "recent_team")
    name_col = _col(df, "full_name", "player_name", "player_display_name")
    pos_col = _col(df, "position")
    status_col = _col(df, "report_status", "game_status", "status")
    gsis_col = _col(df, "gsis_id", "player_id")
    prac_col = _col(df, "practice_status")
    wed_col = _col(df, "date_modified_wed", "wednesday_practice", "practice_wed")
    thu_col = _col(df, "thursday_practice", "practice_thu")
    fri_col = _col(df, "friday_practice", "practice_fri")

    if team_col is None or name_col is None:
        log.warning("availability_feed_unrecognized", columns=list(df.columns)[:20])
        return 0

    rows = df
    if week is not None and week_col:
        rows = rows[pd.to_numeric(rows[week_col], errors="coerce") == week]

    now = datetime.now(timezone.utc)
    written = 0
    for _, r in rows.iterrows():
        status = normalize_status(r.get(status_col) if status_col else None)
        wed = normalize_practice(r.get(wed_col) if wed_col else None)
        thu = normalize_practice(r.get(thu_col) if thu_col else None)
        fri = normalize_practice(
            r.get(fri_col) if fri_col else (r.get(prac_col) if prac_col else None))

        # A row with neither a designation nor any practice information tells
        # us nothing; storing it would only dilute the confidence average.
        if status == "unknown" and not any((wed, thu, fri)):
            continue

        team = canonical_team(r.get(team_col)) or r.get(team_col)
        name = r.get(name_col)
        rw = r.get(week_col) if week_col else None
        try:
            row_week = int(rw) if rw is not None and np.isfinite(float(rw)) else week
        except (TypeError, ValueError):
            row_week = week

        db.add(PlayerAvailability(
            season=season,
            week=row_week,
            team_id=str(team) if team else "",
            player_key=player_value.player_key(name),
            player_name=str(name) if name else None,
            player_id=str(r.get(gsis_col)) if gsis_col and r.get(gsis_col) else None,
            position=str(r.get(pos_col)).upper() if pos_col and r.get(pos_col) else None,
            status=status,
            practice_wed=wed,
            practice_thu=thu,
            practice_fri=fri,
            play_prob=play_probability(status, wed=wed, thu=thu, fri=fri),
            source="nflverse",
            source_detail="injuries",
            confidence=1.0,
            captured_at=now,
            detail={},
        ))
        written += 1

    if written:
        db.commit()
    log.info("availability_refreshed", season=season, week=week, rows=written)
    return written


# ---- Resolve ----------------------------------------------------------------


def resolve_availability(
    db: Session,
    season: int,
    week: int | None,
    *,
    as_of: datetime | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """{team_id: [resolved player rows]} — newest row per source, best source wins."""
    try:
        stmt = select(PlayerAvailability).where(PlayerAvailability.season == season)
        if week is not None:
            stmt = stmt.where(
                (PlayerAvailability.week == week) | (PlayerAvailability.week.is_(None))
            )
        if as_of is not None:
            stmt = stmt.where(PlayerAvailability.captured_at <= as_of)
        rows = db.execute(stmt).scalars().all()
    except Exception as e:  # noqa: BLE001
        log.warning("availability_read_failed", season=season, error=str(e)[:200])
        return {}

    if not rows:
        return {}

    def _aware(dt):
        if dt is None:
            return None
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)

    newest: dict[tuple, PlayerAvailability] = {}
    for r in rows:
        key = (r.team_id, r.player_key, r.source)
        prev = newest.get(key)
        if prev is None or (_aware(r.captured_at) or epoch) > (
                _aware(prev.captured_at) or epoch):
            newest[key] = r

    best: dict[tuple, PlayerAvailability] = {}
    for (team, pkey, source), r in newest.items():
        k = (team, pkey)
        prev = best.get(k)
        if prev is None or SOURCE_PRIORITY.get(source, 0) > SOURCE_PRIORITY.get(
                prev.source, 0):
            best[k] = r

    out: dict[str, list[dict[str, Any]]] = {}
    for (team, _), r in best.items():
        out.setdefault(team, []).append({
            "player_key": r.player_key,
            "player_name": r.player_name,
            "position": r.position,
            "status": r.status,
            "practice": [r.practice_wed, r.practice_thu, r.practice_fri],
            "trajectory": trajectory(r.practice_wed, r.practice_thu, r.practice_fri),
            # Recompute rather than trusting the stored value, so a refit of the
            # mapping applies to the live board immediately. The stored value
            # stays on the row as the record of what we believed at capture.
            "play_prob": play_probability(
                r.status, wed=r.practice_wed, thu=r.practice_thu, fri=r.practice_fri),
            "play_prob_at_capture": r.play_prob,
            "source": r.source,
            "confidence": r.confidence,
            "captured_at": _aware(r.captured_at).isoformat() if r.captured_at else None,
        })
    return out


# ---- Price ------------------------------------------------------------------


async def availability_context(
    db: Session,
    season: int,
    week: int | None,
    *,
    as_of: datetime | None = None,
    teams: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Per-team availability components, ready for ``context_service.persist``.

    Returns one ``availability`` row per team with a report, plus one for every
    team *without* one carrying zero points, zero confidence and a small sigma
    widening — because silence is not health.
    """
    resolved = resolve_availability(db, season, week, as_of=as_of)
    values = await player_value.player_values(db, season)

    try:
        from .. import qb_adjustment_service
        swings = qb_adjustment_service.team_swings(db, season)
    except Exception as e:  # noqa: BLE001
        log.warning("qb_swings_failed", season=season, error=str(e)[:200])
        swings = {}

    cap = _p("context.availability_cap_pts", 6.0)
    unreported_sigma = _p("context.unreported_sigma_mult", 1.03)
    sigma_per_pt = _p("context.availability_sigma_per_pt", 0.012)

    out: list[dict[str, Any]] = []
    covered: set[str] = set()

    for team, players in resolved.items():
        if not team:
            continue
        covered.add(team)
        points = 0.0
        priced: list[dict[str, Any]] = []
        qb_component: dict[str, Any] | None = None

        for pl in players:
            miss = 1.0 - float(pl["play_prob"])
            if miss <= 0.001:
                continue

            pos = (pl.get("position") or "").upper()
            if pos == "QB":
                # A quarterback is only material if he is the starter, and his
                # cost is the swing to the next man up — not his own value.
                swing = swings.get(team) or {}
                starter = (swing.get("starter") or {})
                if not swing.get("applicable") or \
                        starter.get("player_key") != pl["player_key"]:
                    continue
                cost = miss * float(swing.get("swing_pts") or 0.0)
                if cost < 0.01:
                    continue
                # Emitted as its own component, not folded into availability:
                # "-4.1 quarterback" and "-0.6 availability" are two different
                # sentences, and a reader deserves both.
                qb_component = {
                    "team_id": team,
                    "component": "qb",
                    "source": "derived",
                    "points": round(-cost, 3),
                    "sigma_mult": round(
                        1.0 + min(cost * _p("context.qb_sigma_per_pt", 0.02), 0.20), 4),
                    "confidence": 0.8,
                    "detail": {
                        "model_version": AVAILABILITY_MODEL_VERSION,
                        "starter": pl["player_name"],
                        "status": pl["status"],
                        "trajectory": pl["trajectory"],
                        "play_prob": round(pl["play_prob"], 3),
                        "swing_pts": swing.get("swing_pts"),
                        "backup": (swing.get("backup") or {}).get("name"),
                        "backup_is_replacement_level": (
                            swing.get("backup") or {}).get("is_replacement_level"),
                        "cost_pts": round(cost, 3),
                    },
                }
                continue

            val = player_value.value_for(values, pl["player_name"], pos)
            if not val:
                continue
            cost = miss * float(val["value_pts"])
            if cost < 0.01:
                continue
            points -= cost
            priced.append({
                "player": pl["player_name"],
                "position": pos,
                "status": pl["status"],
                "trajectory": pl["trajectory"],
                "play_prob": round(pl["play_prob"], 3),
                "value_pts": val["value_pts"],
                "basis": val["basis"],
                "cost_pts": round(cost, 3),
            })

        if qb_component:
            out.append(qb_component)

        priced.sort(key=lambda x: x["cost_pts"], reverse=True)
        raw = points
        points = max(-cap, min(cap, points))

        # More missing production means a less predictable team, independent of
        # which direction the points went.
        sigma = 1.0 + min(abs(points) * sigma_per_pt, 0.15)

        out.append({
            "team_id": team,
            "component": "availability",
            "source": "derived",
            "points": round(points, 3),
            "sigma_mult": round(sigma, 4),
            "confidence": 0.7 if priced else 0.5,
            "detail": {
                "model_version": AVAILABILITY_MODEL_VERSION,
                "points_uncapped": round(raw, 3),
                "capped": abs(raw) > cap + 1e-9,
                "reported_players": len(players),
                "players": priced[:12],
                "priced_players": len(priced),
            },
        })

    # Teams with no report at all.
    for team in (teams or []):
        t = canonical_team(team) or team
        if not t or t in covered:
            continue
        out.append({
            "team_id": t,
            "component": "availability",
            "source": "derived",
            "points": 0.0,
            "sigma_mult": round(unreported_sigma, 4),
            "confidence": 0.0,
            "detail": {
                "model_version": AVAILABILITY_MODEL_VERSION,
                "unreported": True,
                "note": "no availability report for this team — unreported, not healthy",
            },
        })

    return out


# ---- Fitting the mapping we are currently guessing --------------------------


def fit_play_probability(
    db: Session,
    seasons: list[int],
    snap_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Measure the real status→play mapping from our own snapshots.

    ``snap_rows`` are realized participation records — ``{season, week,
    player_key, played}`` — normally derived from ``import_snap_counts``. Each
    stored availability observation is joined to whether the player actually
    took a snap, and the empirical rate is reported per status and per practice
    trajectory.

    This is the function that turns ``DEFAULT_PLAY_PROB`` from a folk number
    into a measurement. It reports rather than writes: a mapping change moves
    every availability number on the board, so it goes through the param
    registry with a human looking at it.
    """
    played: dict[tuple, bool] = {
        (int(r["season"]), int(r["week"]), r["player_key"]): bool(r["played"])
        for r in snap_rows
        if r.get("player_key") and r.get("week") is not None
    }
    if not played:
        return {"fitted": False, "reason": "no_snap_rows"}

    try:
        rows = db.execute(
            select(PlayerAvailability).where(
                PlayerAvailability.season.in_(seasons))
        ).scalars().all()
    except Exception as e:  # noqa: BLE001
        return {"fitted": False, "reason": "read_failed", "error": str(e)[:200]}

    by_status: dict[str, list[int]] = {}
    by_traj: dict[str, list[int]] = {}
    for r in rows:
        if r.week is None:
            continue
        hit = played.get((r.season, r.week, r.player_key))
        if hit is None:
            continue
        by_status.setdefault(r.status, []).append(1 if hit else 0)
        t = trajectory(r.practice_wed, r.practice_thu, r.practice_fri)
        if t:
            by_traj.setdefault(t, []).append(1 if hit else 0)

    def _summarize(d: dict[str, list[int]], min_n: int) -> dict[str, dict[str, Any]]:
        return {
            k: {"n": len(v), "played_rate": round(sum(v) / len(v), 4)}
            for k, v in sorted(d.items())
            if len(v) >= min_n
        }

    status_fit = _summarize(by_status, 30)
    return {
        "fitted": bool(status_fit),
        "seasons": seasons,
        "matched_observations": sum(len(v) for v in by_status.values()),
        "by_status": status_fit,
        "by_trajectory": _summarize(by_traj, 30),
        "current_priors": {k: DEFAULT_PLAY_PROB.get(k) for k in status_fit},
        "suggested_params": {
            f"context.play_prob_{k}": v["played_rate"]
            for k, v in status_fit.items()
        },
        "note": ("Reported, not applied. Review then set through the param "
                 "registry — this mapping moves every availability number."),
    }
