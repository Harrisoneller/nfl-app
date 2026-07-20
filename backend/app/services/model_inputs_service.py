"""Model-input levers — admin-adjustable INPUTS, not outputs.

Output overrides (spread, total, a stat line) pin one number and leave every
related number inconsistent. Input levers adjust what the model *believes*
about a team or player — pace, efficiency, pass rate, usage shares — and let
the pipeline recompute everything downstream, so a coaching-change adjustment
moves totals, spreads, game scripts, every roster player's projection, prop
probabilities, and fantasy ranks together.

Two lever families (storage: admin_overrides, season-scoped, week IS NULL):

**Team** (`entity_type='team'`): ``pace`` (off plays/gm), ``yards_per_play``,
``pass_rate`` (neutral), ``points_per_game``. Baselines come from the same
PBP aggregates the scoring model reads. Pace and YPP multiply scoring
(points ≈ plays × yds/play × pts/yd): elasticity 1.0 and 0.9, ratios clamped.
``points_per_game`` is a direct level-set that supersedes the multipliers.
``pass_rate`` barely moves totals, so it is a *tilt*: it shifts pass-family
volume up / rush-family volume down (or vice versa) for every player on the
team's roster.

**Player** (`entity_type='player'`, PLAYER_INPUT_FIELDS): ``target_share``,
``rush_share``, ``yards_per_target``, ``yards_per_carry``, ``snap_rate``.
Baselines are computed from the most recent completed season's weekly frame
(+ snap counts). Overrides scale posterior rates by (new / baseline), clamped:
share levers move the whole family distribution; efficiency levers move
yardage 1:1 and TDs with 0.5 elasticity.

Everything degrades gracefully: no override → untouched pipeline; a missing
baseline → that lever is a no-op (surfaced as such in the admin API).
"""
from __future__ import annotations

from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from ..cache import cache
from ..logging_config import get_logger
from ..models.admin_override import PLAYER_INPUT_FIELDS, TEAM_INPUT_FIELDS
from . import overrides_service

log = get_logger(__name__)

CACHE_TTL = 60 * 60 * 6

# ---- Tunables ----------------------------------------------------------------

# Scoring elasticity per team lever (points ≈ plays × yds/play × pts/yd; YPP
# slightly damped — better efficiency also shortens fields / adds TDs but
# costs possessions).
# Registry-backed ("input_levers" category) — constants below are fallback
# documentation; the helpers resolve live admin-tuned values at call time.
_PACE_ELASTICITY = 1.0
_YPP_ELASTICITY = 0.9
# Ratio clamps: a lever can't move scoring by more than ~±25% on its own.
_TEAM_RATIO_CLAMP = (0.78, 1.25)
_TILT_CLAMP = (0.80, 1.25)

# Player lever clamps (ratio of override to baseline).
_SHARE_RATIO_CLAMP = (0.40, 1.75)
_EFF_RATIO_CLAMP = (0.70, 1.40)
_SNAP_RATIO_CLAMP = (0.50, 1.50)
_EFF_TD_ELASTICITY = 0.5


def _p(key: str) -> float:
    from . import param_registry
    return param_registry.value(key)


def _p_clamp(prefix: str) -> tuple[float, float]:
    return (_p(f"levers.{prefix}_lo"), _p(f"levers.{prefix}_hi"))

# Stat families (mirrors the engine's family sets).
_RECV_STATS = ("targets", "receptions", "receiving_yards", "receiving_tds")
_RUSH_STATS = ("carries", "rushing_yards", "rushing_tds")
_PASS_STATS = ("attempts", "completions", "passing_yards", "passing_tds", "interceptions")

TEAM_INPUT_LABELS = {
    "pace": "Offensive plays per game",
    "yards_per_play": "Yards per play",
    "pass_rate": "Neutral-situation pass rate",
    "points_per_game": "Points per game",
}
PLAYER_INPUT_LABELS = {
    "target_share": "Target share",
    "rush_share": "Rush share",
    "yards_per_target": "Yards per target",
    "yards_per_carry": "Yards per carry",
    "snap_rate": "Offensive snap rate",
}

_TEAM_FIELD_TO_AGG = {
    "pace": "off_plays_per_game",
    "yards_per_play": "off_yards_per_play",
    "pass_rate": "pass_rate_neutral",
    "points_per_game": "points_per_game",
}


def _clamp(v: float, lo_hi: tuple[float, float]) -> float:
    return max(lo_hi[0], min(lo_hi[1], v))


# ---- Team levers -------------------------------------------------------------


def team_input_baselines(aggs: dict[str, dict[str, Any]]) -> dict[str, dict[str, float | None]]:
    """Per-team baseline values for every team lever, from PBP aggregates."""
    out: dict[str, dict[str, float | None]] = {}
    for team, a in (aggs or {}).items():
        out[team] = {
            field: (float(a[agg_key]) if a.get(agg_key) is not None else None)
            for field, agg_key in _TEAM_FIELD_TO_AGG.items()
        }
    return out


def adjusted_team_aggregates(
    db: Session, season: int, aggs: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Apply team input levers to the aggregates the scoring model reads.

    Returns a copy with adjusted ``points_per_game`` for overridden teams
    (plus ``_input_adjustment`` metadata for explainability); untouched teams
    share the original dicts. Defense is intentionally not adjustable v1 —
    the levers describe the admin's view of an OFFENSE.
    """
    ovs = overrides_service.team_input_overrides(db, season)
    if not ovs or not aggs:
        return aggs
    out = dict(aggs)
    for team, fields in ovs.items():
        base = dict(out.get(team) or {})
        ppg = base.get("points_per_game")
        if ppg is None:
            ppg = 22.0
        ppg = float(ppg)
        applied: dict[str, Any] = {}

        if "points_per_game" in fields:
            new_ppg = float(fields["points_per_game"])
            applied["points_per_game"] = {"from": round(ppg, 1), "to": round(new_ppg, 1)}
            ppg = new_ppg
        else:
            mult = 1.0
            for field, elasticity in (
                ("pace", _p("levers.pace_elasticity")),
                ("yards_per_play", _p("levers.ypp_elasticity")),
            ):
                if field not in fields:
                    continue
                baseline = base.get(_TEAM_FIELD_TO_AGG[field])
                if baseline is None or float(baseline) <= 0:
                    applied[field] = {"skipped": "no baseline"}
                    continue
                ratio = _clamp(float(fields[field]) / float(baseline), _p_clamp("team_ratio_clamp"))
                mult *= ratio ** elasticity
                applied[field] = {
                    "from": round(float(baseline), 2),
                    "to": round(float(fields[field]), 2),
                    "scoring_multiplier": round(ratio ** elasticity, 3),
                }
            if mult != 1.0:
                applied["points_per_game_effect"] = {
                    "from": round(ppg, 1), "to": round(ppg * mult, 1),
                }
                ppg *= mult

        if "pass_rate" in fields:
            applied["pass_rate"] = {
                "from": base.get("pass_rate_neutral"),
                "to": float(fields["pass_rate"]),
                "note": "applied as pass/rush volume tilt on roster players",
            }

        base["points_per_game"] = ppg
        base["_input_adjustment"] = applied
        out[team] = base
    return out


def team_pass_tilts(
    db: Session, season: int, aggs: dict[str, dict[str, Any]],
) -> dict[str, dict[str, float]]:
    """{team: {"pass": mult, "rush": mult}} from pass_rate overrides.

    A pass-rate change is roughly volume-neutral in points but redistributes
    plays: pass-family volume scales by new/base, rush-family by the
    complement ratio. Empty dict when no pass_rate override exists.
    """
    ovs = overrides_service.team_input_overrides(db, season)
    out: dict[str, dict[str, float]] = {}
    for team, fields in ovs.items():
        if "pass_rate" not in fields:
            continue
        base = (aggs.get(team) or {}).get("pass_rate_neutral")
        base = float(base) if base else 0.55
        new = float(fields["pass_rate"])
        tilt_clamp = _p_clamp("tilt_clamp")
        pass_mult = _clamp(new / base, tilt_clamp)
        rush_mult = _clamp((1.0 - new) / max(1e-6, 1.0 - base), tilt_clamp)
        out[team] = {"pass": pass_mult, "rush": rush_mult}
    return out


# ---- Player usage baselines ---------------------------------------------------


async def player_usage_baselines(season: int) -> dict[str, dict[str, float | None]]:
    """{gsis_id: {target_share, rush_share, yards_per_target, yards_per_carry,
    snap_rate}} from the season's weekly frame + snap counts. Cached 6h.

    Callers pass the most recent completed season — shares describe the
    established role the levers adjust away from.
    """
    key = f"player_usage_baselines:{season}"
    if (v := cache.get(key)) is not None:
        return v

    # Late import: player_predictions_service imports us lazily too; both
    # sides at call time only, so there is no import cycle at module load.
    from . import player_predictions_service as proj

    df = await proj._player_weekly_frame(season)  # noqa: SLF001
    if df is None or len(df) == 0:
        return {}

    out: dict[str, dict[str, float | None]] = {}
    need = {"player_id", "recent_team"}
    if not need.issubset(df.columns):
        return {}

    g = df.groupby("player_id")
    sums = {}
    for col in ("targets", "carries", "receiving_yards", "rushing_yards"):
        sums[col] = g[col].sum() if col in df.columns else None
    team_of = g["recent_team"].last()

    # Team totals for share denominators.
    team_targets = (
        df.groupby("recent_team")["targets"].sum() if "targets" in df.columns else None
    )
    team_carries = (
        df.groupby("recent_team")["carries"].sum() if "carries" in df.columns else None
    )

    for pid in team_of.index:
        team = team_of.get(pid)
        tgt = float(sums["targets"].get(pid, 0) or 0) if sums["targets"] is not None else 0.0
        car = float(sums["carries"].get(pid, 0) or 0) if sums["carries"] is not None else 0.0
        rec_y = float(sums["receiving_yards"].get(pid, 0) or 0) if sums["receiving_yards"] is not None else 0.0
        rush_y = float(sums["rushing_yards"].get(pid, 0) or 0) if sums["rushing_yards"] is not None else 0.0
        t_tot = float(team_targets.get(team, 0) or 0) if team_targets is not None and team else 0.0
        c_tot = float(team_carries.get(team, 0) or 0) if team_carries is not None and team else 0.0
        out[str(pid)] = {
            "target_share": round(tgt / t_tot, 4) if t_tot > 0 and tgt > 0 else None,
            "rush_share": round(car / c_tot, 4) if c_tot > 0 and car > 0 else None,
            "yards_per_target": round(rec_y / tgt, 2) if tgt >= 20 else None,
            "yards_per_carry": round(rush_y / car, 2) if car >= 25 else None,
            "snap_rate": None,  # filled below when snap counts are available
        }

    # Snap rates (best-effort; separate nflverse dataset keyed by pfr ids but
    # carries gsis-style `player` names — join on offense_pct via gsis when
    # present, else skip).
    try:
        from ..adapters.data.nfl_data_py_adapter import NflDataPyAdapter

        snaps = await NflDataPyAdapter().snap_counts_df(season)
        if snaps is not None and len(snaps) and "offense_pct" in snaps.columns:
            id_col = next(
                (c for c in ("gsis_id", "player_gsis_id", "player_id") if c in snaps.columns),
                None,
            )
            if id_col:
                snap_rate = snaps.groupby(id_col)["offense_pct"].mean()
                for pid, rate in snap_rate.items():
                    pid = str(pid)
                    if pid in out and pd.notna(rate):
                        out[pid]["snap_rate"] = round(float(rate), 4)
    except Exception as e:  # noqa: BLE001 — snap data is enrichment
        log.info("snap_counts_unavailable", error=str(e)[:160])

    cache.set(key, out, CACHE_TTL)
    return out


# ---- Player lever application --------------------------------------------------


def player_stat_multipliers(
    levers: dict[str, float],
    baselines: dict[str, float | None] | None,
    tilt: dict[str, float] | None,
) -> dict[str, float]:
    """Combined per-stat multiplier from usage levers + team pass tilt.

    Each lever contributes (override / baseline), clamped; levers with no
    baseline are no-ops. Multipliers compose multiplicatively per stat.
    """
    mults: dict[str, float] = {}

    def bump(stats: tuple[str, ...], ratio: float) -> None:
        for s in stats:
            mults[s] = mults.get(s, 1.0) * ratio

    b = baselines or {}
    for field, value in (levers or {}).items():
        base = b.get(field)
        if field == "snap_rate":
            if base:
                bump(_RECV_STATS + _RUSH_STATS + _PASS_STATS,
                     _clamp(float(value) / float(base), _p_clamp("snap_ratio_clamp")))
        elif field == "target_share":
            if base:
                bump(_RECV_STATS, _clamp(float(value) / float(base), _p_clamp("share_ratio_clamp")))
        elif field == "rush_share":
            if base:
                bump(_RUSH_STATS, _clamp(float(value) / float(base), _p_clamp("share_ratio_clamp")))
        elif field == "yards_per_target":
            if base:
                r = _clamp(float(value) / float(base), _p_clamp("eff_ratio_clamp"))
                bump(("receiving_yards",), r)
                bump(("receiving_tds",), r ** _p("levers.eff_td_elasticity"))
        elif field == "yards_per_carry":
            if base:
                r = _clamp(float(value) / float(base), _p_clamp("eff_ratio_clamp"))
                bump(("rushing_yards",), r)
                bump(("rushing_tds",), r ** _p("levers.eff_td_elasticity"))

    if tilt:
        bump(_PASS_STATS + _RECV_STATS, tilt.get("pass", 1.0))
        bump(_RUSH_STATS, tilt.get("rush", 1.0))

    return {s: m for s, m in mults.items() if abs(m - 1.0) > 1e-9}


async def player_input_context(db: Session, season: int) -> dict[str, Any]:
    """Everything the projection pipelines need to apply player-side levers:

    {"overrides": {player_id: {field: value}},
     "baselines": {gsis_id: {...}},
     "tilts": {team_id: {"pass": m, "rush": m}}}

    Cheap when nothing is overridden (baselines only computed if needed).
    """
    from ..utils.seasons import latest_completed_season

    empty: dict[str, Any] = {"overrides": {}, "baselines": {}, "tilts": {}}

    overrides = overrides_service.player_input_overrides(db, season)
    team_ovs = overrides_service.team_input_overrides(db, season)
    needs_tilts = any("pass_rate" in f for f in team_ovs.values())
    if not overrides and not needs_tilts:
        # Fast path (and the only path most requests take): nothing overridden
        # → no aggregate/frame fetches, no behavior change anywhere.
        return empty

    try:
        tilts: dict[str, dict[str, float]] = {}
        if needs_tilts:
            from . import analytics_service

            aggs = await analytics_service._team_pbp_aggregates(  # noqa: SLF001
                season, allow_live_fallback=False,
            )
            if not aggs:
                aggs = await analytics_service._team_pbp_aggregates(  # noqa: SLF001
                    season - 1, allow_live_fallback=False,
                )
            tilts = team_pass_tilts(db, season, aggs or {})

        baselines: dict[str, dict[str, float | None]] = {}
        if overrides:
            usage_season = min(season - 1, latest_completed_season())
            if season <= latest_completed_season():
                usage_season = season
            baselines = await player_usage_baselines(usage_season)

        return {"overrides": overrides, "baselines": baselines, "tilts": tilts}
    except Exception as e:  # noqa: BLE001 — levers must never break a board build
        log.warning("player_input_context_failed", error=str(e)[:200])
        return empty
