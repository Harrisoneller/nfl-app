"""What is each player worth, in points per game, above his replacement?

This is the valuation half of the availability layer. ``availability.py`` says
who is not playing; this says what that costs.

The three decisions that make or break an injury model
-----------------------------------------------------

**1. Replacement level, not zero.** A player's absence costs the difference
between him and whoever takes his snaps — never his whole production. Value is
measured against a positional replacement percentile computed from this
season's own player pool, not against an average or against nothing.

**2. Redistribution.** An injured WR1's targets do not evaporate; they go to
WR2 and the tight end. Charging a team the receiver's full value-above-
replacement assumes his ten targets are simply deleted from the offense, which
is not what happens. The redistribution factor (``context.redistribution``,
default 0.55) is the fraction of his edge that is actually lost once the
offense reallocates. This single number is why most public injury adjustments
are roughly twice as large as they should be, and it is the first thing to
fit against closing-line movement once the CLV harness exists.

**3. Snap share is the weight, and the NFL actually has it.** A 30%-snap
rotational receiver and a 95%-snap WR1 with identical efficiency are not worth
the same. ``import_snap_counts`` gives real participation rates, which the
college model has to approximate. Use them.

What this module does NOT claim
-------------------------------
Offensive linemen have no per-player EPA and no public charting in the data we
ingest, so they get a positional prior scaled by snap share, not a measurement.
Defenders are worse: the nflverse seasonal frame is offense-shaped, so
individual defensive value here is also a prior, deliberately capped tight.
Both are marked ``basis="prior"`` in the output so nothing downstream mistakes
them for a measurement, and both are candidates for a real upgrade via
``import_pfr_data`` (pressure rate, coverage snaps) in a later phase.

Model version ``pv-nfl-v1``. Reads materialized rows first (no network);
falls back to building the frame from nflverse on a cold store.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from ...logging_config import get_logger
from ...utils.teams import canonical_team

log = get_logger(__name__)

PLAYER_VALUE_MODEL_VERSION = "pv-nfl-v1"

# ---- Position taxonomy ------------------------------------------------------

QB_POSITIONS = {"QB"}
SKILL_POSITIONS = {"RB", "FB", "HB", "WR", "TE"}
OL_POSITIONS = {"OL", "OT", "OG", "OC", "C", "G", "T", "LT", "RT", "LG", "RG"}
DL_POSITIONS = {"DL", "DE", "DT", "NT", "EDGE"}
LB_POSITIONS = {"LB", "ILB", "OLB", "MLB"}
DB_POSITIONS = {"DB", "CB", "S", "FS", "SS", "NB"}
SPECIALIST_POSITIONS = {"K", "P", "PK", "LS"}

DEFENSE_POSITIONS = DL_POSITIONS | LB_POSITIONS | DB_POSITIONS


def unit_for_position(pos: str | None) -> str:
    p = (pos or "").upper().strip()
    if p in QB_POSITIONS:
        return "qb"
    if p in SKILL_POSITIONS:
        return "skill"
    if p in OL_POSITIONS:
        return "ol"
    if p in DEFENSE_POSITIONS:
        return "def"
    if p in SPECIALIST_POSITIONS:
        return "specialist"
    return "other"


# ---- Positional priors ------------------------------------------------------
#
# Points per game a full-time starter at this position is worth above the man
# who would replace him, BEFORE redistribution. These are priors, used where we
# cannot measure: the whole offensive line, every defender, and specialists.
#
# The ordering encodes what the public evidence supports rather than what fans
# feel: an edge rusher and a left tackle move a game meaningfully, an off-ball
# linebacker and a safety barely move it at all, and a kicker is worth more
# than most people would guess but still under a point.
_POSITION_PRIOR_PTS: dict[str, float] = {
    # Offensive line — tackles over interior, protection over movement.
    "LT": 0.70, "RT": 0.55, "OT": 0.60, "T": 0.60,
    "LG": 0.40, "RG": 0.40, "OG": 0.40, "G": 0.40,
    "C": 0.50, "OC": 0.50, "OL": 0.50,
    # Front seven — edge pressure is the one defensive role with a large,
    # repeatable effect on team scoring.
    "EDGE": 0.75, "DE": 0.70, "DT": 0.45, "NT": 0.35, "DL": 0.55,
    "OLB": 0.50, "ILB": 0.30, "MLB": 0.30, "LB": 0.35,
    # Secondary — CB1 is the exception worth pricing; safeties are not.
    "CB": 0.50, "NB": 0.30, "DB": 0.40, "S": 0.30, "FS": 0.30, "SS": 0.30,
    # Specialists.
    "K": 0.45, "PK": 0.45, "P": 0.15, "LS": 0.10,
}
_DEFAULT_PRIOR_PTS = 0.30

# Hard ceilings per player, applied after redistribution. A single non-QB is
# never worth more than this to a team's expected margin. The purpose is not
# realism at the extremes — it is that one bad row in a feed must not be able
# to move a line by a touchdown.
_MAX_SKILL_PTS = 2.2
_MAX_PRIOR_PTS = 1.2


def _p(key: str, default: float) -> float:
    try:
        from .. import param_registry
        return float(param_registry.value(key))
    except Exception:  # noqa: BLE001 — must run without a DB
        return default


def _finite(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if np.isfinite(f) else default


def _percentile(values: list[float], pct: float) -> float:
    vals = sorted(v for v in values if np.isfinite(v))
    if not vals:
        return 0.0
    return float(np.percentile(vals, pct))


def player_key(name: str | None) -> str:
    """Normalized join key for a player name.

    Injury reports carry names, the stats frame carries gsis ids, and the two
    only meet through the name. Lowercase, strip punctuation and generational
    suffixes — 'A.J. Brown', 'AJ Brown' and 'A.J. Brown Jr.' must all collide.
    """
    if not name:
        return ""
    s = str(name).lower().strip()
    for ch in ".,'`’-":
        s = s.replace(ch, "" if ch in ".'`’" else " ")
    parts = [t for t in s.split() if t not in {"jr", "sr", "ii", "iii", "iv", "v"}]
    return ".".join(parts)


# ---- Snap share -------------------------------------------------------------


def _snap_shares(snaps: pd.DataFrame | None, recent_weeks: int = 5) -> dict[str, float]:
    """Mean recent snap share per player.

    Recency-weighted by truncation rather than by decay: a player's role over
    the last few games is what matters, and averaging across a whole season
    understates someone who took over a starting job in October.
    """
    if snaps is None or len(snaps) == 0:
        return {}
    df = snaps.copy()
    if "week" in df.columns:
        weeks = pd.to_numeric(df["week"], errors="coerce")
        cutoff = weeks.max() - recent_weeks + 1 if np.isfinite(weeks.max()) else None
        if cutoff is not None:
            df = df[weeks >= cutoff]
    pct_col = next((c for c in ("offense_pct", "defense_pct", "pct") if c in df.columns), None)
    name_col = next((c for c in ("player", "player_name", "full_name") if c in df.columns), None)
    if pct_col is None or name_col is None:
        return {}
    out: dict[str, list[float]] = {}
    for _, r in df.iterrows():
        key = player_key(r.get(name_col))
        if not key:
            continue
        # nflverse reports these as fractions in some seasons and percents in
        # others; normalize rather than trusting either.
        v = _finite(r.get(pct_col), np.nan)
        if not np.isfinite(v):
            continue
        if v > 1.5:
            v /= 100.0
        out.setdefault(key, []).append(min(max(v, 0.0), 1.0))
    return {k: float(np.mean(v)) for k, v in out.items() if v}


# ---- Offensive skill valuation ----------------------------------------------


def _skill_values(
    df: pd.DataFrame,
    snap_share: dict[str, float],
) -> dict[str, dict[str, Any]]:
    """Points/game above positional replacement for RB/WR/TE.

    value = (epa_per_play − replacement_epa_per_play) × opportunities_per_game

    EPA is already denominated in points, so no conversion constant is needed
    and none is introduced — one fewer number to be wrong about.
    """
    out: dict[str, dict[str, Any]] = {}
    if df is None or len(df) == 0:
        return out

    pos_col = "position" if "position" in df.columns else None
    if pos_col is None:
        return out

    games_col = "games" if "games" in df.columns else None
    min_opps = _p("context.value_min_opportunities", 20.0)
    pct = _p("context.replacement_percentile", 25.0)

    # Build the eligible pool once per position so replacement level is
    # measured against real players at that position this season.
    rows: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        pos = str(r.get(pos_col) or "").upper().strip()
        if unit_for_position(pos) != "skill":
            continue
        opps = (
            _finite(r.get("targets")) + _finite(r.get("carries"))
        )
        if opps < min_opps:
            continue
        games = max(_finite(r.get(games_col), 0.0), 1.0) if games_col else 1.0
        epp = _finite(r.get("epa_per_play"), np.nan)
        if not np.isfinite(epp):
            continue
        name = r.get("player_display_name") or r.get("player_name")
        rows.append({
            "key": player_key(name),
            "name": name,
            "position": pos,
            "team": canonical_team(r.get("team")) or r.get("team"),
            "epa_per_play": epp,
            "opps_per_game": opps / games,
        })

    if not rows:
        return out

    by_pos: dict[str, list[float]] = {}
    for r in rows:
        by_pos.setdefault(r["position"], []).append(r["epa_per_play"])
    replacement = {p: _percentile(v, pct) for p, v in by_pos.items()}

    redistribution = _p("context.redistribution", 0.55)

    for r in rows:
        if not r["key"]:
            continue
        edge = r["epa_per_play"] - replacement.get(r["position"], 0.0)
        share = snap_share.get(r["key"])
        # No snap data: fall back to volume alone rather than assuming a role.
        weight = share if share is not None else 1.0
        raw = edge * r["opps_per_game"] * weight
        value = max(0.0, raw) * redistribution
        out[r["key"]] = {
            "player_key": r["key"],
            "name": r["name"],
            "position": r["position"],
            "team_id": r["team"],
            "unit": "skill",
            "basis": "measured",
            "value_pts": round(min(value, _MAX_SKILL_PTS), 3),
            "detail": {
                "epa_per_play": round(r["epa_per_play"], 4),
                "replacement_epa_per_play": round(
                    replacement.get(r["position"], 0.0), 4),
                "opportunities_per_game": round(r["opps_per_game"], 2),
                "snap_share": round(share, 3) if share is not None else None,
                "redistribution": redistribution,
                "value_before_redistribution": round(max(0.0, raw), 3),
            },
        }
    return out


# ---- Prior-based valuation (OL, defense, specialists) -----------------------


def _prior_values(
    roster: pd.DataFrame | None,
    snap_share: dict[str, float],
    already_valued: set[str],
) -> dict[str, dict[str, Any]]:
    """Positional priors for everyone we cannot measure.

    Scaled by snap share so a rotational defensive lineman is not charged as an
    every-down starter, and capped hard. Marked ``basis="prior"`` so the
    explainability panel can say so out loud.
    """
    out: dict[str, dict[str, Any]] = {}
    if roster is None or len(roster) == 0:
        return out

    redistribution = _p("context.redistribution", 0.55)
    name_col = next(
        (c for c in ("player_name", "full_name", "player_display_name")
         if c in roster.columns), None)
    if name_col is None or "position" not in roster.columns:
        return out

    for _, r in roster.iterrows():
        pos = str(r.get("position") or "").upper().strip()
        unit = unit_for_position(pos)
        if unit in ("qb", "skill", "other"):
            continue
        key = player_key(r.get(name_col))
        if not key or key in already_valued or key in out:
            continue
        share = snap_share.get(key)
        # A player with no snap record is not assumed to be a starter. Priors
        # describe starters, so an unknown role gets a fraction of one.
        weight = share if share is not None else 0.4
        prior = _POSITION_PRIOR_PTS.get(pos, _DEFAULT_PRIOR_PTS)
        value = prior * weight * redistribution
        out[key] = {
            "player_key": key,
            "name": r.get(name_col),
            "position": pos,
            "team_id": canonical_team(r.get("team")) or r.get("team"),
            "unit": unit,
            "basis": "prior",
            "value_pts": round(min(value, _MAX_PRIOR_PTS), 3),
            "detail": {
                "positional_prior_pts": prior,
                "snap_share": round(share, 3) if share is not None else None,
                "snap_share_assumed": share is None,
                "redistribution": redistribution,
                "note": "positional prior — no per-player measurement available",
            },
        }
    return out


# ---- Public entry -----------------------------------------------------------


async def player_values(
    db: Session,
    season: int,
) -> dict[str, dict[str, Any]]:
    """{player_key: valuation} for every non-QB we can put a number on.

    Reads the materialized seasonal frame first (no network). Returns an empty
    dict rather than raising if nothing is available — an availability layer
    with no valuations prices everything at zero, which is the correct failure
    mode: we say nothing rather than something wrong.
    """
    try:
        from .. import analytics_service, materialize_service
        from ...adapters.data.nfl_data_py_adapter import NflDataPyAdapter

        df = materialize_service.load_player_dataframe(db, season)
        if df is None or len(df) == 0:
            df = await analytics_service.build_seasonal_player_dataframe(season)
        if df is None or len(df) == 0:
            log.info("player_value_no_frame", season=season)
            return {}

        nfl = NflDataPyAdapter()
        snaps = await nfl.snap_counts_df(season)
        roster = await nfl.rosters_df(season)
        share = _snap_shares(snaps)

        values = _skill_values(df, share)
        values.update(_prior_values(roster, share, set(values)))
        log.info("player_value_built", season=season, players=len(values),
                 measured=sum(1 for v in values.values() if v["basis"] == "measured"))
        return values
    except Exception as e:  # noqa: BLE001 — valuation must never break predictions
        log.warning("player_value_failed", season=season, error=str(e)[:200])
        return {}


def value_for(
    values: dict[str, dict[str, Any]],
    name: str | None,
    position: str | None = None,
) -> dict[str, Any] | None:
    """Look up a valuation by player name, falling back to a positional prior.

    A player on the injury report who is not in the valuation set is common and
    expected (practice-squad callups, rookies, anyone with no volume). Falling
    back to the prior is better than dropping him: 'we do not have a number for
    this guy' is not the same as 'this guy is worth nothing'.
    """
    key = player_key(name)
    if key and key in values:
        return values[key]
    if not position:
        return None
    pos = position.upper().strip()
    unit = unit_for_position(pos)
    if unit in ("qb", "other"):
        return None
    prior = _POSITION_PRIOR_PTS.get(pos, _DEFAULT_PRIOR_PTS)
    value = prior * 0.4 * _p("context.redistribution", 0.55)
    return {
        "player_key": key,
        "name": name,
        "position": pos,
        "unit": unit,
        "basis": "prior_unmatched",
        "value_pts": round(min(value, _MAX_PRIOR_PTS), 3),
        "detail": {
            "positional_prior_pts": prior,
            "note": "no season valuation matched this player; positional prior applied",
        },
    }
