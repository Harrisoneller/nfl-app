"""NFL luck index from the shrunk residuals in ``features.nfl.defs``.

The index does not read market or odds columns, and it does not refit the
luck residuals. Sample size is already inside those residuals (they are
shrunk toward the baseline by ``n / (n + k)``). This module z-scores each
residual across the teams, averages the z-scores, and z-scores that average.
"""
from __future__ import annotations

import asyncio
from typing import Any

import numpy as np
import pandas as pd

from ..logging_config import get_logger
from ..utils.seasons import current_or_upcoming_season, latest_completed_season
from ..utils.teams import canonical_team
from . import artifact_cache

log = get_logger(__name__)

KIND = "team_luck"
# Live seasons are refreshed on the worker. 18h covers a missed 12h gap.
LIVE_CACHE_TTL = 60 * 60 * 18
LIVE_L1_TTL = 60 * 30
CACHE_TTL = 60 * 60 * 6

TITLE = "Regression candidate"
DISCLAIMER = (
    "This is not a pick. It describes results that sat above or below a "
    "baseline. It does not say who will win."
)
METHOD = (
    "Each piece is a residual the feature contract already shrunk toward its "
    "baseline, so a small sample counts for less. Each piece is then a "
    "z-score across the teams in this season. The index is the z-score of "
    "the average of those pieces. Positive is luckier than the league."
)

# Spec items the contract does not publish. They stay omitted.
OMITTED: tuple[dict[str, str], ...] = (
    {
        "id": "turnover_margin_vs_havoc",
        "reason": (
            "Turnover margin versus a havoc or turnover-worthy-play rate is "
            "not in the feature contract. Interceptions versus passes "
            "defended is the turnover piece that is."
        ),
    },
    {
        "id": "points_per_opportunity_vs_success",
        "reason": (
            "Points per scoring opportunity versus success rate is not in "
            "the feature contract. Red-zone touchdown rate versus the league "
            "rate is the scoring-opportunity piece that is."
        ),
    },
)

# luck column, sample-size column, shrinkage k from features.nfl.defs.
_COMPONENTS: tuple[dict[str, Any], ...] = (
    {
        "id": "one_score",
        "luck": "one_score_luck",
        "n": "one_score_n",
        "k": 16.0,
        "label": "One-score games",
        "note": (
            "Wins in games decided by 8 points or fewer, compared with "
            "winning half of them."
        ),
    },
    {
        "id": "fumble",
        "luck": "fumble_rec_luck",
        "n": "fumble_n",
        "k": 50.0,
        "label": "Fumble recoveries",
        "note": (
            "Share of fumbles this team recovered, compared with about 50%. "
            "Forced and unforced fumbles both count. This is not a havoc count."
        ),
    },
    {
        "id": "interception",
        "luck": "int_luck",
        "n": "int_n",
        "k": 200.0,
        "label": "Interceptions vs passes defended",
        "note": (
            "Interceptions compared with the rate expected from passes "
            "defended. Fewer interceptions than that expectation is the "
            "lucky side."
        ),
    },
    {
        "id": "red_zone",
        "luck": "rz_td_luck",
        "n": "rz_n",
        "k": 30.0,
        "label": "Red-zone touchdowns",
        "note": (
            "Red-zone trips that became touchdowns, compared with the "
            "league rate."
        ),
    },
    {
        "id": "field_goal",
        "luck": "fg_luck",
        "n": "fg_n",
        "k": 40.0,
        "label": "Field goals",
        "note": (
            "Field goals made, compared with a frozen make rate for the "
            "kick distance."
        ),
    },
)


class FeatureDefsUnavailable(RuntimeError):
    """``features.nfl.defs`` is not importable. Luck must not be invented."""


def _load_defs() -> Any:
    try:
        from features.nfl import defs as feature_defs
    except ImportError as exc:
        log.error("feature_defs_import_failed", error=str(exc))
        raise FeatureDefsUnavailable(
            "features.nfl.defs could not be imported. "
            "The luck index is unavailable until that package is installed."
        ) from exc
    return feature_defs


def _num(value: Any, ndigits: int | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(number):
        return None
    if ndigits is None:
        return number
    return round(number, ndigits)


def _zscore(values: np.ndarray) -> np.ndarray:
    """Population z-score. Non-finite inputs stay non-finite. A flat column is 0."""
    out = np.full(values.shape, np.nan, dtype="float64")
    finite = np.isfinite(values)
    data = values[finite]
    if data.size == 0:
        return out
    if data.size == 1 or float(np.std(data, ddof=0)) == 0.0:
        out[finite] = 0.0
        return out
    mean = float(np.mean(data))
    std = float(np.std(data, ddof=0))
    out[finite] = (data - mean) / std
    return out


def _present_components(frame: pd.DataFrame) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    present: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    for spec in _COMPONENTS:
        if spec["luck"] in frame.columns and spec["n"] in frame.columns:
            present.append(spec)
        else:
            missing.append({
                "id": spec["id"],
                "reason": f"{spec['label']} is not in this season's feature table.",
            })
    return present, missing


def score_season(frame: pd.DataFrame) -> dict[str, Any]:
    """League luck table from a team-season frame. Higher is luckier."""
    if not isinstance(frame, pd.DataFrame) or frame.empty or "team" not in frame.columns:
        return _empty_bundle(reason="No team-season luck rows for this season.")
    present, missing_components = _present_components(frame)
    omitted = list(OMITTED) + missing_components
    if not present:
        bundle = _empty_bundle(reason="The feature table has none of the luck columns.")
        bundle["omitted"] = omitted
        return bundle

    work = frame.copy()
    z_columns: list[str] = []
    for spec in present:
        residual = pd.to_numeric(work[spec["luck"]], errors="coerce").to_numpy(dtype="float64")
        z_name = f"{spec['id']}__z"
        work[z_name] = _zscore(residual)
        z_columns.append(z_name)

    z_matrix = work[z_columns].to_numpy(dtype="float64")
    finite_cells = np.isfinite(z_matrix)
    counts = finite_cells.sum(axis=1)
    summed = np.where(finite_cells, z_matrix, 0.0).sum(axis=1)
    means = np.divide(summed, counts, out=np.full(counts.shape, np.nan), where=counts > 0)
    work["luck_mean"] = means
    work["luck_z"] = _zscore(np.asarray(means, dtype="float64"))

    rows: list[dict[str, Any]] = []
    for record in work.to_dict(orient="records"):
        team = canonical_team(record.get("team")) or str(record.get("team") or "")
        score = _num(record.get("luck_z"))
        components = []
        for spec in present:
            residual = _num(record.get(spec["luck"]))
            samples = _num(record.get(spec["n"]))
            if residual is None:
                continue
            sample_n = int(samples) if samples is not None else 0
            k = float(spec["k"])
            components.append({
                "id": spec["id"],
                "label": spec["label"],
                "note": spec["note"],
                "residual": round(residual, 4),
                "z": _num(record.get(f"{spec['id']}__z"), 2),
                "samples": sample_n,
                "shrink_k": k,
                "shrink_weight": round(sample_n / (sample_n + k), 3) if sample_n >= 0 else None,
            })
        rows.append({
            "team": team,
            "score": None if score is None else round(score, 3),
            "side": _side(score),
            "components": components,
        })

    ranked = [row for row in rows if row["score"] is not None]
    ranked.sort(key=lambda row: (-float(row["score"]), row["team"]))
    n = len(ranked)
    for index, row in enumerate(ranked):
        better = sum(1 for other in ranked if float(other["score"]) > float(row["score"]))
        row["rank"] = better + 1
        row["league_size"] = n
        row["place"] = index + 1
    unranked = [row for row in rows if row["score"] is None]
    for row in unranked:
        row["rank"] = None
        row["league_size"] = n
        row["place"] = None
    ordered = ranked + unranked
    return {
        "league": "NFL",
        "title": TITLE,
        "disclaimer": DISCLAIMER,
        "scale": {"negative": "Unlucky", "positive": "Lucky"},
        "method": METHOD,
        "source": "features.nfl.defs",
        "placeholder": False,
        "cacheable": True,
        "omitted": omitted,
        "components": [
            {"id": spec["id"], "label": spec["label"], "note": spec["note"]}
            for spec in present
        ],
        "teams": ordered,
        "unavailable": [],
    }


def _side(score: float | None) -> str | None:
    if score is None:
        return None
    if score > 0:
        return "Lucky"
    if score < 0:
        return "Unlucky"
    return "Even"


def _empty_bundle(*, reason: str, unavailable_id: str = "play_by_play") -> dict[str, Any]:
    return {
        "league": "NFL",
        "title": TITLE,
        "disclaimer": DISCLAIMER,
        "scale": {"negative": "Unlucky", "positive": "Lucky"},
        "method": METHOD,
        "source": "features.nfl.defs",
        "placeholder": False,
        "cacheable": False,
        "omitted": list(OMITTED),
        "components": [],
        "teams": [],
        "unavailable": [{"id": unavailable_id, "reason": reason}],
        "error": reason,
    }


def _project_plays(plays: pd.DataFrame, allowlist: set[str]) -> pd.DataFrame:
    keep = [col for col in plays.columns if str(col) in allowlist]
    return plays.loc[:, keep].copy()


def _cache_key(season: int) -> str:
    return f"nfl:{season}:luck:v1"


def _ttl(season: int) -> int | None:
    if season < current_or_upcoming_season():
        return None
    return LIVE_CACHE_TTL


def _l1_ttl(season: int) -> int:
    ttl = _ttl(season)
    if ttl is None:
        return CACHE_TTL
    return min(ttl, LIVE_L1_TTL)


def _loader() -> Any:
    from ..adapters.data.nfl_data_py_adapter import NflDataPyAdapter

    return NflDataPyAdapter()


async def _compute(season: int) -> dict[str, Any]:
    feature_defs = _load_defs()
    plays = await _loader().pbp_df(season)
    if not isinstance(plays, pd.DataFrame) or plays.empty:
        return _empty_bundle(reason="Play-by-play for this season is not in the data pipeline.")
    allowlist = {str(col) for col in feature_defs.PBP_INPUT_COLUMNS}
    clean = _project_plays(plays, allowlist)
    try:
        _game, season_frame = await asyncio.to_thread(feature_defs.build_features, clean)
    except Exception as exc:  # noqa: BLE001 — a bad feature build must not 500 the page
        log.warning("luck_features_failed", season=season, error=str(exc)[:200])
        return _empty_bundle(
            reason="The feature module failed while building luck columns, so the index is omitted.",
            unavailable_id="feature_defs",
        )
    bundle = score_season(season_frame if isinstance(season_frame, pd.DataFrame) else pd.DataFrame())
    bundle["season"] = season
    if not bundle.get("teams"):
        bundle["cacheable"] = False
    return bundle


async def league_luck(season: int) -> dict[str, Any]:
    """League luck table, cached. A missing feature module is not cached."""
    try:
        _load_defs()
    except FeatureDefsUnavailable as exc:
        log.error("feature_defs_unavailable", season=season, error=str(exc))
        bundle = _empty_bundle(reason=str(exc), unavailable_id="feature_defs")
        bundle["season"] = season
        return bundle
    bundle = await artifact_cache.get_or_compute(
        kind=KIND,
        key=_cache_key(season),
        compute=lambda: _compute(season),
        ttl_seconds=_ttl(season),
        l1_ttl_seconds=_l1_ttl(season),
    )
    bundle["season"] = season
    if not bundle.get("cacheable", True):
        _drop(season)
    return bundle


def team_view(bundle: dict[str, Any], team_id: str) -> dict[str, Any]:
    """One team plus the league table, luckiest first."""
    team_id = canonical_team(team_id) or team_id
    teams = list(bundle.get("teams") or [])
    row = next((item for item in teams if item.get("team") == team_id), None)
    out = {
        "team_id": team_id,
        "season": bundle.get("season"),
        "league": "NFL",
        "title": bundle.get("title") or TITLE,
        "disclaimer": bundle.get("disclaimer") or DISCLAIMER,
        "scale": bundle.get("scale") or {"negative": "Unlucky", "positive": "Lucky"},
        "method": bundle.get("method") or METHOD,
        "source": bundle.get("source"),
        "omitted": list(bundle.get("omitted") or []),
        "components": list(bundle.get("components") or []),
        "unavailable": list(bundle.get("unavailable") or []),
        "team": row,
        "table": teams,
    }
    if row is None and not out["unavailable"]:
        out["error"] = f"No luck row for {team_id} in {bundle.get('season')}."
    elif bundle.get("error"):
        out["error"] = bundle["error"]
    return out


async def team_luck(team_id: str, season: int) -> dict[str, Any]:
    return team_view(await league_luck(season), team_id)


def _drop(season: int) -> None:
    from ..db import SessionLocal

    try:
        db = SessionLocal()
        try:
            artifact_cache.evict(db, KIND, _cache_key(season))
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001 — cache maintenance must not sink the read
        log.debug("team_luck_evict_failed", error=str(exc)[:160])
        artifact_cache.l1_cache.delete(f"artifact:{KIND}:{_cache_key(season)}")


def cache_status(season: int) -> str:
    from ..cache import cache

    key = f"artifact:{KIND}:{_cache_key(season)}"
    return "hit" if cache.get(key) is not None else "miss"


async def materialize(_db: Any = None) -> dict[str, Any]:
    """Worker job. A missing ``features.nfl.defs`` raises."""
    del _db
    _load_defs()
    seasons: list[int] = []
    for season in (latest_completed_season(), current_or_upcoming_season()):
        if season not in seasons:
            seasons.append(season)
    live = current_or_upcoming_season()
    if live in seasons:
        _drop(live)
    rows = 0
    for season in seasons:
        bundle = await league_luck(season)
        rows += len(bundle.get("teams") or [])
    return {"rows": rows, "seasons": len(seasons), "source": "features.nfl.defs"}
