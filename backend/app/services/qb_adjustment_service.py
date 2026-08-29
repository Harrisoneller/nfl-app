"""Quarterback pricing: who is starting, and what the next man up costs.

Why this is its own service
---------------------------
Every other injury is a positional-average question. The quarterback is not.
The backup is a specific known person with his own measured history, the swing
between him and the starter is several times larger than any other single
absence, and neither Elo nor season-aggregate EPA has any idea it happened —
both describe a team that, as far as they know, is still the team that produced
the history.

A backup quarterback is worth somewhere around four to seven points of spread.
That is the largest number in this entire codebase that the model previously
could not see.

How the depth chart is decided
------------------------------
**Usage first, posted depth chart second.** nflverse depth charts have been
intermittently unavailable across seasons, and in any case a posted chart is a
weaker statement about who actually plays than attempts and snaps are. So the
pecking order is derived from dropback volume, recency-weighted, and the posted
chart is consulted only to break ties and to place a rookie or new signing who
has no attempts yet.

How value is computed
---------------------
EPA per dropback, shrunk toward the league mean by attempts, blended across the
current and prior season, then multiplied by dropbacks per game to land in
points. EPA is already denominated in points so there is no conversion constant
to get wrong.

The shrinkage matters more here than anywhere else in the model. A quarterback
with 60 attempts has an EPA/play that is mostly noise; taken at face value it
will hand you a backup who looks like an MVP or a starter who looks unplayable,
and it will do so most often exactly when a starter has just gone down and the
sample is smallest. ``context.qb_prior_attempts`` is the regularizer.

What is NOT here yet
--------------------
**Offseason de-attribution.** When a team changes starting quarterback between
seasons, its prior-season offensive rating should not carry forward unadjusted
— that team's history was produced by a different player. This is the
FiveThirtyEight QB-adjusted-Elo idea and it belongs with the preseason-prior
work rather than the in-season availability path, because it changes how
ratings are *built* rather than how a single game is adjusted.
``carryover_note()`` marks the seam.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from ..logging_config import get_logger
from ..utils.teams import canonical_team
from .context.player_value import player_key

log = get_logger(__name__)

QB_MODEL_VERSION = "qb-nfl-v1"

# Import-safe defaults; live values resolve through the registry at call time.
QB_DROPBACKS_PER_GAME = 35.0
QB_PRIOR_ATTEMPTS = 220.0
QB_REPLACEMENT_EPA = -0.12
QB_PRIOR_SEASON_WEIGHT = 0.5
QB_MAX_SWING_PTS = 9.0
# Dropbacks at which a quarterback is treated as an established starter for the
# purpose of choosing his shrinkage target. See `_shrink_target`.
QB_STARTER_DROPBACKS = 600.0


def _p(key: str, default: float) -> float:
    try:
        from . import param_registry
        return float(param_registry.value(key))
    except Exception:  # noqa: BLE001
        return default


def _finite(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if np.isfinite(f) else default


# ---- Building the QB pool ---------------------------------------------------


def _qb_rows(df: pd.DataFrame | None) -> list[dict[str, Any]]:
    """Extract per-QB attempts and EPA/dropback from a seasonal player frame."""
    if df is None or len(df) == 0 or "position" not in df.columns:
        return []
    rows: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        if str(r.get("position") or "").upper().strip() != "QB":
            continue
        attempts = _finite(r.get("attempts"))
        if attempts <= 0:
            continue
        # Sacks are dropbacks too, and a quarterback who takes them is worse
        # than one who does not — including them keeps the denominator honest.
        dropbacks = attempts + _finite(r.get("sacks"))
        epa = _finite(r.get("passing_epa"), np.nan)
        if not np.isfinite(epa):
            # Fall back to the frame's own per-play figure when the passing
            # split is missing.
            epp = _finite(r.get("epa_per_play"), np.nan)
            if not np.isfinite(epp):
                continue
            epa = epp * dropbacks
        name = r.get("player_display_name") or r.get("player_name")
        rows.append({
            "key": player_key(name),
            "name": name,
            "player_id": r.get("player_id"),
            "team_id": canonical_team(r.get("team")) or r.get("team"),
            "dropbacks": dropbacks,
            "epa": epa,
            "epa_per_dropback": epa / dropbacks if dropbacks > 0 else 0.0,
        })
    return rows


def _shrink_target(dropbacks: float, league: float, replacement: float) -> float:
    """The prior mean a quarterback should be regressed toward.

    Shrinking every quarterback toward the league mean is the obvious choice
    and it is wrong, in a direction that matters. The league mean is dominated
    by starters, so a backup with 25 attempts gets pulled up to roughly
    starter-average — which says a team losing its starter barely suffers,
    when the whole reason he is the backup is that he is not that good.

    So the target itself slides with experience: a quarterback nobody has seen
    regresses toward replacement level, an established starter regresses toward
    the league mean, and everyone in between is interpolated. This is what
    keeps the starter-to-backup swing in the four-to-seven point range the
    market actually prices rather than collapsing it toward zero.
    """
    threshold = _p("context.qb_starter_dropbacks", QB_STARTER_DROPBACKS)
    if threshold <= 0:
        return league
    w = min(1.0, max(0.0, dropbacks / threshold))
    return replacement + w * (league - replacement)


def _league_mean(rows: list[dict[str, Any]]) -> float:
    """Attempt-weighted league mean EPA/dropback — the shrinkage target."""
    total_db = sum(r["dropbacks"] for r in rows)
    if total_db <= 0:
        return 0.0
    return sum(r["epa"] for r in rows) / total_db


def _shrink(total_epa: float, dropbacks: float, league: float, prior: float) -> float:
    """Regress a quarterback's rate toward the league mean by his own volume.

    Standard empirical-Bayes form: add ``prior`` pseudo-dropbacks of
    league-average production to what he actually did, then divide by the
    combined volume.

        (observed_total_EPA + league_rate x prior) / (dropbacks + prior)

    ``total_epa`` is a *total*, not a rate — the numerator is already in EPA,
    so it must not be multiplied by the dropback count again.
    """
    if dropbacks + prior <= 0:
        return league
    return (total_epa + league * prior) / (dropbacks + prior)


def qb_pool(
    db: Session,
    season: int,
) -> dict[str, list[dict[str, Any]]]:
    """{team_id: [QBs, best-used first]} with shrunk, blended value in points.

    Blends the current season with the prior one (discounted) so that Week 2
    is not priced off 40 attempts, and so a quarterback who changed teams
    carries his own history with him rather than inheriting his new team's.
    """
    try:
        from . import materialize_service

        cur = _qb_rows(materialize_service.load_player_dataframe(db, season))
        prev = _qb_rows(materialize_service.load_player_dataframe(db, season - 1))
    except Exception as e:  # noqa: BLE001
        log.warning("qb_pool_failed", season=season, error=str(e)[:200])
        return {}

    if not cur and not prev:
        return {}

    league = _league_mean(cur or prev)
    replacement = _p("context.qb_replacement_epa", QB_REPLACEMENT_EPA)
    prior_w = _p("context.qb_prior_season_weight", QB_PRIOR_SEASON_WEIGHT)
    prior_att = _p("context.qb_prior_attempts", QB_PRIOR_ATTEMPTS)
    dbs_per_game = _p("context.qb_dropbacks_per_game", QB_DROPBACKS_PER_GAME)

    prev_by_key = {r["key"]: r for r in prev}

    merged: dict[str, dict[str, Any]] = {}
    for r in cur:
        p = prev_by_key.get(r["key"])
        dropbacks = r["dropbacks"] + (p["dropbacks"] * prior_w if p else 0.0)
        epa = r["epa"] + (p["epa"] * prior_w if p else 0.0)
        # Career volume, not just this season's, decides how much benefit of the
        # doubt he gets — a veteran backup is a known quantity, a rookie is not.
        career = r["dropbacks"] + (p["dropbacks"] if p else 0.0)
        target = _shrink_target(career, league, replacement)
        shrunk = _shrink(epa, dropbacks, target, prior_att)
        merged[r["key"]] = {
            **r,
            "blended_dropbacks": dropbacks,
            "epa_per_dropback_shrunk": shrunk,
            # Points per game this quarterback is worth relative to a
            # replacement-level starter.
            "value_pts": (shrunk - _p("context.qb_replacement_epa",
                                      QB_REPLACEMENT_EPA)) * dbs_per_game,
            "season_dropbacks": r["dropbacks"],
        }

    # Quarterbacks with prior-season history but no attempts yet this season:
    # a backup who has not played, or a starter who just changed teams. They
    # are priced off their own past, heavily shrunk, not off a positional
    # average — that is the entire point of doing this by player.
    for key, p in prev_by_key.items():
        if key in merged:
            continue
        dropbacks = p["dropbacks"] * prior_w
        target = _shrink_target(p["dropbacks"], league, replacement)
        shrunk = _shrink(p["epa"] * prior_w, dropbacks, target, prior_att)
        merged[key] = {
            **p,
            "blended_dropbacks": dropbacks,
            "epa_per_dropback_shrunk": shrunk,
            "value_pts": (shrunk - _p("context.qb_replacement_epa",
                                      QB_REPLACEMENT_EPA)) * dbs_per_game,
            "season_dropbacks": 0.0,
            "from_prior_season_only": True,
        }

    by_team: dict[str, list[dict[str, Any]]] = {}
    for r in merged.values():
        if not r.get("team_id"):
            continue
        by_team.setdefault(r["team_id"], []).append(r)

    # Order by *this season's* usage — who is actually taking the snaps now —
    # falling back to blended volume for players who have not played yet.
    for team, qbs in by_team.items():
        qbs.sort(key=lambda q: (q["season_dropbacks"], q["blended_dropbacks"]),
                 reverse=True)
    return by_team


# ---- The number that matters ------------------------------------------------


def replacement_value_pts() -> float:
    """Points/game for a quarterback we know nothing about.

    Zero by construction: ``value_pts`` is already measured against the
    replacement baseline. Stated as a function so the meaning is explicit at
    every call site rather than a bare 0.0 that looks like a placeholder.
    """
    return 0.0


def starter_swing(
    qbs: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """What a team loses if its starter cannot play.

    Returns the starter, the next man up, and the point swing between them —
    the difference, never the starter's absolute value. A team whose backup is
    nearly as good as the starter loses almost nothing, and a model that
    charges the starter's full value cannot express that.
    """
    empty = {"applicable": False, "swing_pts": 0.0,
             "starter": None, "backup": None}
    if not qbs:
        return empty

    starter = qbs[0]
    backup = qbs[1] if len(qbs) > 1 else None
    backup_value = backup["value_pts"] if backup else replacement_value_pts()

    swing = float(starter["value_pts"]) - float(backup_value)
    cap = _p("context.qb_max_swing_pts", QB_MAX_SWING_PTS)
    raw = swing
    # The swing is a loss, never a gain: if the backup grades out higher than
    # the starter, that is a small sample talking, not a real upgrade, and we
    # decline to bet on it.
    swing = max(0.0, min(swing, cap))

    return {
        "applicable": True,
        "swing_pts": round(swing, 3),
        "swing_pts_uncapped": round(raw, 3),
        "capped": raw > cap,
        "starter": {
            "name": starter.get("name"),
            "player_key": starter.get("key"),
            "value_pts": round(float(starter["value_pts"]), 3),
            "epa_per_dropback": round(
                float(starter["epa_per_dropback_shrunk"]), 4),
            "season_dropbacks": round(float(starter.get("season_dropbacks") or 0), 1),
        },
        "backup": {
            "name": backup.get("name") if backup else None,
            "player_key": backup.get("key") if backup else None,
            "value_pts": round(float(backup_value), 3),
            "epa_per_dropback": round(
                float(backup["epa_per_dropback_shrunk"]), 4) if backup else None,
            "season_dropbacks": round(
                float(backup.get("season_dropbacks") or 0), 1) if backup else 0.0,
            "is_replacement_level": backup is None,
        },
    }


def team_swings(db: Session, season: int) -> dict[str, dict[str, Any]]:
    """{team_id: starter_swing} for every team we can build a QB room for."""
    pool = qb_pool(db, season)
    return {team: starter_swing(qbs) for team, qbs in pool.items()}


def carryover_note() -> str:
    """The seam where offseason QB de-attribution belongs.

    Elo and adjusted EPA both carry a team's prior-season strength forward on
    the assumption that the same quarterback produced it and will produce it
    again. When a team changes starters between seasons, that assumption is
    false and the carried-forward rating is wrong in a knowable direction.
    Fixing it means adjusting how ratings are *built* (the preseason prior),
    not how one game is adjusted, so it is not done here.
    """
    return (
        "Offseason QB de-attribution is not implemented. Prior-season Elo and "
        "adjusted EPA carry forward unadjusted through a starter change. See "
        "docs/CONTEXT_LAYER_ROADMAP.md P0."
    )
