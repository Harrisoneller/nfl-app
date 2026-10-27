"""Team efficiency profile: cache, job, and page shape.

Metric definitions live in ``features/nfl/defs.py``. This module loads
play-by-play, hands it to ``nfl_feature_adapter``, and stores the team-game
and team-season tables. Percentile ranks and the four-game line are
presentation over those columns. They are not EPA, success-rate, or
opponent-adjustment math.

Until defs lands, the adapter returns the placeholder contract and this
page omits every rate instead of inventing one. Garbage time is reported
as excluded only when the loaded module says its filter ran.
"""
from __future__ import annotations

import json
import math
from typing import Any

import pandas as pd

from ..logging_config import get_logger
from ..utils.seasons import current_or_upcoming_season, latest_completed_season
from ..utils.teams import canonical_team
from . import artifact_cache
from . import nfl_feature_adapter as features
from .analytics_service import percentile_rank

log = get_logger(__name__)

KIND = "team_efficiency"
CACHE_TTL = 60 * 60 * 6  # current season, real feature module
PLACEHOLDER_TTL = 60 * 30  # short, so a landed defs module replaces the stub quickly

# Display rule only: a rank needs a sample. Not a definition of the metric.
MIN_PLAYS_FOR_RANK = 40

_TEAM_COLUMNS = ("posteam", "defteam", "home_team", "away_team", "team", "opponent")

WAITING_REASON = (
    "Opponent-adjusted EPA, success rate, and schedule strength are omitted. "
    "features/nfl/defs.py is not in this build yet, and this page does not "
    "calculate those metrics itself."
)

_HIGHER_IS_BETTER = {
    "epa": {"offense": True, "defense": False},
    "success_rate": {"offense": True, "defense": False},
    "schedule_strength": {"offense": True, "defense": True},
}


def _num(value: Any, ndigits: int = 4) -> float | None:
    number = _finite(value)
    if number is None:
        return None
    return round(number, ndigits)


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _cell(
    value: float | None,
    distribution: list[float],
    *,
    higher_is_better: bool,
    plays: int | None,
    rank_sense: str,
) -> dict[str, Any]:
    sample = plays if plays is not None else 0
    usable = [item for item in distribution if item is not None]
    percentile = None
    if value is not None and sample >= MIN_PLAYS_FOR_RANK and usable:
        percentile = percentile_rank(value, usable, higher_is_better)
    return {
        "value": _num(value),
        "percentile": _num(percentile, 1),
        "higher_is_better": higher_is_better,
        "plays": int(sample) if plays is not None else None,
        "rank_sense": rank_sense,
    }


def _empty_cell(higher_is_better: bool, rank_sense: str) -> dict[str, Any]:
    return _cell(None, [], higher_is_better=higher_is_better, plays=None, rank_sense=rank_sense)


def _empty_rolling(reason: str | None = None) -> dict[str, Any]:
    return {
        "metric": "epa_per_play",
        "season_average": None,
        "last4_average": None,
        "games": [],
        "empty_reason": reason,
    }


def _public_filters(raw: dict[str, Any], *, placeholder: bool) -> dict[str, Any]:
    excluded = bool(raw.get("garbage_time_excluded")) and not placeholder
    return {
        "garbage_time_excluded": excluded,
        "wp_low": raw.get("wp_low", 0.10),
        "wp_high": raw.get("wp_high", 0.90),
        "description": raw.get("description") or "",
        "plays_kept": raw.get("plays_kept") if excluded else None,
        "plays_excluded": raw.get("plays_excluded") if excluded else None,
        "preseason_excluded": bool(raw.get("preseason_excluded")) and excluded,
    }


def _canonicalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    out = frame.copy()
    for col in _TEAM_COLUMNS:
        if col not in out.columns:
            continue
        out[col] = out[col].map(lambda value: canonical_team(value) if isinstance(value, str) else value)
    return out


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    clean = features.drop_market(frame)
    if not isinstance(clean, pd.DataFrame) or clean.empty:
        return []
    payload = json.loads(clean.to_json(orient="records"))
    return payload if isinstance(payload, list) else []


def _lookup(row: dict[str, Any] | pd.Series, column: str | None) -> Any:
    if not column:
        return None
    if isinstance(row, pd.Series):
        if column not in row.index:
            return None
        value = row[column]
    else:
        value = row.get(column)
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    return value


def _weighted(values: list[float | None], weights: list[float]) -> float | None:
    numerator = 0.0
    denominator = 0.0
    plain: list[float] = []
    for value, weight in zip(values, weights):
        if value is None:
            continue
        plain.append(value)
        if weight > 0:
            numerator += value * weight
            denominator += weight
    if denominator > 0:
        return numerator / denominator
    if plain:
        return sum(plain) / len(plain)
    return None


def _location(row: dict[str, Any], game_bind: dict[str, str]) -> str | None:
    raw = _lookup(row, game_bind.get("location"))
    if isinstance(raw, str) and raw.strip():
        text = raw.strip().lower()
        if text in ("home", "away"):
            return text
    flag = _finite(_lookup(row, game_bind.get("is_home")))
    if flag is None:
        return None
    return "home" if flag > 0 else "away"


def _game_label(week: float | None, opponent: str | None, location: str | None) -> str:
    where = "@" if location == "away" else "vs"
    week_text = f"Wk {int(week)} " if week is not None else ""
    return f"{week_text}{where} {opponent or '—'}".strip()


def _rolling_unit(
    games: list[dict[str, Any]],
    *,
    value_key: str,
    plays_key: str,
    empty_reason: str | None,
) -> dict[str, Any]:
    if not games:
        return _empty_rolling(empty_reason)
    ordered = sorted(
        games,
        key=lambda row: (
            _finite(row.get("week")) is None,
            _finite(row.get("week")) or 0,
            str(row.get("game_id") or ""),
        ),
    )
    values = [_finite(row.get(value_key)) for row in ordered]
    weights = [(_finite(row.get(plays_key)) or 0.0) for row in ordered]
    season_avg = _weighted(values, weights)
    last4 = _weighted(values[-4:], weights[-4:]) if len(ordered) >= 4 else None
    rows: list[dict[str, Any]] = []
    for index, game in enumerate(ordered):
        window = slice(max(0, index - 3), index + 1)
        rolling = _weighted(values[window], weights[window]) if index >= 3 else None
        plays = weights[index]
        rows.append({
            "week": _num(game.get("week"), 0) if _finite(game.get("week")) is not None else None,
            "game_id": game.get("game_id"),
            "opponent": game.get("opponent"),
            "location": game.get("location"),
            "label": game.get("label") or "",
            "plays": int(plays) if plays else 0,
            "value": _num(values[index]),
            "rolling4": _num(rolling),
            "season_average": _num(season_avg),
        })
    return {
        "metric": "epa_per_play",
        "season_average": _num(season_avg),
        "last4_average": _num(last4),
        "games": rows,
        "empty_reason": None,
    }


def _column_map(frame: pd.DataFrame, team_column: str) -> dict[str, dict[str, Any]]:
    if frame.empty or team_column not in frame.columns:
        return {}
    grouped: dict[str, dict[str, Any]] = {}
    for _, row in frame.iterrows():
        team = _lookup(row, team_column)
        if not isinstance(team, str) or not team:
            continue
        grouped[team] = row.to_dict()
    return grouped


def _present_teams(
    season_frame: pd.DataFrame,
    game_frame: pd.DataFrame,
    *,
    placeholder: bool,
    empty_reason: str | None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Turn feature rows into the page's splits and rolling series."""
    bind = features.bindings()
    season_bind = bind["season"]
    game_bind = bind["game"]
    unavailable: list[dict[str, str]] = []
    if placeholder or season_frame.empty and game_frame.empty:
        return {}, unavailable

    by_team = _column_map(season_frame, season_bind["team"])
    teams = sorted(by_team)
    split_rows: list[dict[str, Any]] = []
    for split_id, spec in season_bind["splits"].items():
        split_rows.append({
            "id": split_id,
            "label": spec.get("label") or split_id,
            "approximated": False,
            "approximation_note": None,
            "spec": spec,
        })

    missing: list[str] = []
    if teams:
        sample = next(iter(by_team.values()))
        for row in split_rows:
            for side in ("offense", "defense"):
                for column in row["spec"][side].values():
                    if column not in sample:
                        missing.append(column)
    if missing:
        unavailable.append({
            "id": "feature_columns",
            "reason": (
                "Some feature-contract columns are not in this season's table, "
                "so those cells are blank: " + ", ".join(sorted(set(missing))) + "."
            ),
        })

    per_team: dict[str, dict[str, Any]] = {
        team: {"splits": [], "rolling": {"offense": _empty_rolling(empty_reason), "defense": _empty_rolling(empty_reason)}}
        for team in teams
    }

    for row in split_rows:
        distributions: dict[tuple[str, str], list[float]] = {}
        for side in ("offense", "defense"):
            for metric, column in row["spec"][side].items():
                if metric not in _HIGHER_IS_BETTER:
                    continue
                rankable: list[float] = []
                for team in teams:
                    record = by_team[team]
                    plays = _finite(_lookup(record, row["spec"][side]["plays"]))
                    value = _finite(_lookup(record, column))
                    if value is None or plays is None or plays < MIN_PLAYS_FOR_RANK:
                        continue
                    rankable.append(value)
                distributions[(side, metric)] = rankable
        for team in teams:
            record = by_team[team]
            block: dict[str, Any] = {}
            for side in ("offense", "defense"):
                plays = _finite(_lookup(record, row["spec"][side]["plays"]))
                play_count = int(plays) if plays is not None else None
                metrics: dict[str, Any] = {}
                for metric in ("epa", "success_rate", "schedule_strength"):
                    column = row["spec"][side][metric]
                    sense = "tougher" if metric == "schedule_strength" else "better"
                    higher = _HIGHER_IS_BETTER[metric][side]
                    metrics[metric] = _cell(
                        _finite(_lookup(record, column)),
                        distributions.get((side, metric), []),
                        higher_is_better=higher,
                        plays=play_count,
                        rank_sense=sense,
                    )
                block[side] = metrics
            per_team[team]["splits"].append({
                "id": row["id"],
                "label": row["label"],
                "approximated": False,
                "approximation_note": None,
                "offense": block["offense"],
                "defense": block["defense"],
            })

    game_rows = _records(game_frame) if not game_frame.empty else []
    by_game: dict[str, list[dict[str, Any]]] = {}
    for record in game_rows:
        team = record.get(game_bind["team"])
        if isinstance(team, str):
            team = canonical_team(team)
        if not isinstance(team, str) or not team:
            continue
        week = _finite(record.get(game_bind["week"]))
        opponent = record.get(game_bind["opponent"])
        if isinstance(opponent, str):
            opponent = canonical_team(opponent)
        location = _location(record, game_bind)
        by_game.setdefault(team, []).append({
            "week": week,
            "game_id": record.get(game_bind["game_id"]),
            "opponent": opponent,
            "location": location,
            "label": _game_label(week, opponent if isinstance(opponent, str) else None, location),
            "off_epa": record.get(game_bind["offense_epa"]),
            "def_epa": record.get(game_bind["defense_epa"]),
            "off_plays": record.get(game_bind["offense_plays"]),
            "def_plays": record.get(game_bind["defense_plays"]),
        })
        if team not in per_team:
            per_team[team] = {
                "splits": [],
                "rolling": {
                    "offense": _empty_rolling(empty_reason),
                    "defense": _empty_rolling(empty_reason),
                },
            }

    chart_reason = empty_reason or "No games in the feature table for this season."
    for team, payload in per_team.items():
        rows = by_game.get(team, [])
        payload["rolling"] = {
            "offense": _rolling_unit(
                rows, value_key="off_epa", plays_key="off_plays", empty_reason=chart_reason,
            ),
            "defense": _rolling_unit(
                rows, value_key="def_epa", plays_key="def_plays", empty_reason=chart_reason,
            ),
        }
    return per_team, unavailable


def assemble_bundle(
    team_season: pd.DataFrame,
    team_game: pd.DataFrame,
    season: int,
    *,
    filters: dict[str, Any],
    source: str,
    placeholder: bool,
) -> dict[str, Any]:
    """Cache document: feature tables plus the per-team page payload."""
    season_frame = features.drop_market(_canonicalize_frame(team_season))
    game_frame = features.drop_market(_canonicalize_frame(team_game))
    public = _public_filters(filters, placeholder=placeholder)
    notes: list[str] = []
    unavailable: list[dict[str, str]] = []
    chart_reason = "No games to chart for this season."
    if placeholder:
        unavailable.append({"id": "feature_defs", "reason": WAITING_REASON})
        chart_reason = (
            "Game-by-game EPA is omitted until features/nfl/defs.py is available."
        )
        notes.append(
            "The efficiency tab is waiting on the NFL feature contract. "
            "No rates were calculated for this page."
        )
    elif season_frame.empty and game_frame.empty:
        unavailable.append({
            "id": "feature_rows",
            "reason": f"The feature module returned no team rows for {season}.",
        })
    else:
        notes.append(
            "Rates come from the NFL feature contract. "
            "Percentiles and the four-game line are presentation over those columns."
        )

    teams, missing = _present_teams(
        season_frame,
        game_frame,
        placeholder=placeholder,
        empty_reason=chart_reason,
    )
    unavailable.extend(missing)
    return {
        "league": "NFL",
        "season": season,
        "source": source,
        "placeholder": placeholder,
        "cacheable": True,
        "filters": public,
        "notes": notes,
        "unavailable": unavailable,
        "team_season_rows": len(season_frame.index),
        "team_game_rows": len(game_frame.index),
        "team_season": _records(season_frame),
        "team_game": _records(game_frame),
        "teams": teams,
    }


def project_team(bundle: dict[str, Any], team_id: str, season: int) -> dict[str, Any]:
    """One team, in the shape the page already renders."""
    team_id = canonical_team(team_id) if team_id else team_id
    rolling_reason = None
    if bundle.get("placeholder"):
        rolling_reason = (
            "Game-by-game EPA is omitted until features/nfl/defs.py is available."
        )
    out: dict[str, Any] = {
        "team_id": team_id,
        "season": season,
        "league": bundle.get("league") or "NFL",
        "source": bundle.get("source"),
        "filters": bundle.get("filters") or _public_filters({}, placeholder=True),
        "notes": list(bundle.get("notes") or []),
        "unavailable": list(bundle.get("unavailable") or []),
        "splits": [],
        "rolling": {
            "offense": _empty_rolling(rolling_reason),
            "defense": _empty_rolling(rolling_reason),
        },
    }
    teams = bundle.get("teams") or {}
    team = teams.get(team_id)
    if team is None:
        if not bundle.get("placeholder"):
            if not teams:
                out["error"] = "No efficiency profile for this season."
            else:
                out["error"] = f"No feature row for {team_id} in {season}."
        return out
    out["splits"] = team.get("splits") or []
    out["rolling"] = team.get("rolling") or out["rolling"]
    return out


# ---------------------------------------------------------------------------
# Load, cache, job
# ---------------------------------------------------------------------------


_loader: Any = None


def _pbp_loader() -> Any:
    """Lazy so importing this module does not import nfl-data-py."""
    global _loader
    if _loader is None:
        from ..adapters.data.nfl_data_py_adapter import NflDataPyAdapter

        _loader = NflDataPyAdapter()
    return _loader


def _cache_key(season: int) -> str:
    return f"nfl:{season}"


def _ttl(season: int) -> int | None:
    if features.using_placeholder():
        return PLACEHOLDER_TTL
    if season < current_or_upcoming_season():
        return None
    return CACHE_TTL


def _no_pbp(season: int) -> dict[str, Any]:
    return {
        "league": "NFL",
        "season": season,
        "source": features.source_name(),
        "placeholder": False,
        "cacheable": False,
        "filters": _public_filters(
            {
                "garbage_time_excluded": False,
                "description": "Play-by-play for this season is not in the data pipeline.",
            },
            placeholder=True,
        ),
        "notes": [],
        "unavailable": [{
            "id": "play_by_play",
            "reason": "Play-by-play for this season is not in the data pipeline.",
        }],
        "team_season_rows": 0,
        "team_game_rows": 0,
        "team_season": [],
        "team_game": [],
        "teams": {},
        "error": "No play-by-play is available for this season yet.",
    }


async def _load_plays(season: int) -> pd.DataFrame | None:
    frame = await _pbp_loader().pbp_df(season)
    if frame is None or len(frame) == 0:
        return None
    return _canonicalize_frame(frame)


async def _compute_features(season: int) -> dict[str, Any]:
    """Run the adapter for one season. The placeholder skips the download."""
    placeholder = features.using_placeholder()
    if placeholder:
        plays = pd.DataFrame()
    else:
        plays = await _load_plays(season)
        if plays is None:
            return _no_pbp(season)
    try:
        season_frame = features.team_season(plays)
        game_frame = features.team_game(plays)
    except Exception as exc:  # noqa: BLE001 — a bad feature module must not 500 the page
        log.warning("team_features_failed", season=season, error=str(exc)[:200])
        bundle = _no_pbp(season)
        bundle["cacheable"] = False
        bundle["unavailable"] = [{
            "id": "feature_defs",
            "reason": "The feature module failed while building team tables, so the rates are omitted.",
        }]
        return bundle
    bundle = assemble_bundle(
        season_frame if isinstance(season_frame, pd.DataFrame) else pd.DataFrame(),
        game_frame if isinstance(game_frame, pd.DataFrame) else pd.DataFrame(),
        season,
        filters=features.filters(),
        source=features.source_name(),
        placeholder=placeholder,
    )
    bundle["cacheable"] = True
    return bundle


async def league_features(season: int) -> dict[str, Any]:
    """League feature bundle, L1 then L2, singleflighted.

    An empty download is not cached. A placeholder result uses a short TTL
    so it does not stick after defs.py is deployed.
    """
    bundle = await artifact_cache.get_or_compute(
        kind=KIND,
        key=_cache_key(season),
        compute=lambda: _compute_features(season),
        ttl_seconds=_ttl(season),
        l1_ttl_seconds=min(_ttl(season) or CACHE_TTL, CACHE_TTL),
    )
    if not bundle.get("cacheable", True):
        _drop_bundle_cache(season)
    return bundle


def _drop_bundle_cache(season: int) -> None:
    from ..db import SessionLocal

    try:
        db = SessionLocal()
        try:
            artifact_cache.evict(db, KIND, _cache_key(season))
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001 — cache maintenance must not sink the read
        log.debug("team_efficiency_evict_failed", error=str(exc)[:160])
        artifact_cache.l1_cache.delete(f"artifact:{KIND}:{_cache_key(season)}")


def cache_status(season: int) -> str:
    """L1 hit/miss for the response header. Checked before the read."""
    from ..cache import cache

    key = f"artifact:{KIND}:{_cache_key(season)}"
    return "hit" if cache.get(key) is not None else "miss"


async def efficiency_profile(team_id: str, season: int) -> dict[str, Any]:
    bundle = await league_features(season)
    profile = project_team(bundle, team_id, season)
    if bundle.get("error") and "error" not in profile:
        profile["error"] = bundle["error"]
    return profile


async def materialize_profiles(_db: Any = None) -> dict[str, Any]:
    """Worker job: refresh the live season and warm the latest completed one.

    The request path reads this cache. A cold page still computes through
    the same function, singleflighted, so the job is not required to render.
    """
    del _db
    seasons: list[int] = []
    for season in (latest_completed_season(), current_or_upcoming_season()):
        if season not in seasons:
            seasons.append(season)
    live = current_or_upcoming_season()
    if live in seasons:
        _drop_bundle_cache(live)
    rows = 0
    source = features.source_name()
    for season in seasons:
        bundle = await league_features(season)
        rows += int(bundle.get("team_season_rows") or 0)
        source = str(bundle.get("source") or source)
    return {"rows": rows, "seasons": len(seasons), "source": source}
