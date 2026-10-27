"""Team efficiency profile: cache, job, and page shape.

Numbers come from ``features.nfl.defs.build_features``. This module loads
play-by-play, strips market columns, stores the team-game and team-season
tables, and shapes one team for the page. It does not compute EPA, success
rate, opponent adjustment, or percentiles.

Headline cells are the opponent-adjusted ratings (``*_off_adj`` /
``*_def_adj`` and ``sched_strength``). Percentiles are the contract's
``*_season_pct`` columns. The chart is ``*_last4`` against ``*_season`` on
the team-game rows, which are as of the start of each week. Early-down and
passing-down rows are the published season rates; the contract has no
opponent-adjusted rating for those splits.
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

log = get_logger(__name__)

KIND = "team_efficiency"
CACHE_TTL = 60 * 60 * 6
PLACEHOLDER_TTL = 60 * 30

WAITING_REASON = (
    "Opponent-adjusted EPA, success rate, and schedule strength are omitted. "
    "features/nfl/defs.py is not in this build yet, and this page does not "
    "calculate those metrics itself."
)

_TEAM_COLUMNS = ("posteam", "defteam", "home_team", "away_team", "team", "opponent")

# column, percentile column, display, higher_is_better, rank sense, percentile caption
_Cell = tuple[str | None, str | None, str, bool, str, str | None]

def _cell(
    column: str | None,
    percentile: str | None,
    display: str,
    higher: bool,
    sense: str = "better",
    percentile_of: str | None = None,
) -> _Cell:
    return (column, percentile, display, higher, sense, percentile_of)


# Early and passing downs have season rates and percentiles, not a ridge rating.
_RATE_NOTE = (
    "This row is the season rate from the feature contract, not an "
    "opponent-adjusted rating. The adjusted ratings are all plays, pass, and rush."
)

_SPLITS: tuple[dict[str, Any], ...] = (
    {
        "id": "overall",
        "label": "All plays",
        "adjusted": True,
        "note": None,
        "offense": {
            "epa": _cell("epa_off_adj", "epa_play_season_pct", "epa", True, "better", "the season rate"),
            "success_rate": _cell("sr_off_adj", "success_rate_season_pct", "signed", True, "better", "the season rate"),
            "schedule_strength": _cell("sched_strength", "sched_strength_season_pct", "signed", True),
        },
        "defense": {
            "epa": _cell("epa_def_adj", None, "epa", False),
            "success_rate": _cell("sr_def_adj", None, "signed", False),
            "schedule_strength": _cell("sched_strength", "sched_strength_season_pct", "signed", True),
        },
    },
    {
        "id": "pass",
        "label": "Pass",
        "adjusted": True,
        "note": None,
        "offense": {
            "epa": _cell("pass_off_adj", "pass_epa_season_pct", "epa", True, "better", "the season rate"),
            "success_rate": _cell(None, None, "signed", True),
            "schedule_strength": _cell(None, None, "signed", True),
        },
        "defense": {
            "epa": _cell("pass_def_adj", None, "epa", False),
            "success_rate": _cell(None, None, "signed", False),
            "schedule_strength": _cell(None, None, "signed", True),
        },
    },
    {
        "id": "rush",
        "label": "Rush",
        "adjusted": True,
        "note": None,
        "offense": {
            "epa": _cell("rush_off_adj", "rush_epa_season_pct", "epa", True, "better", "the season rate"),
            "success_rate": _cell(None, None, "signed", True),
            "schedule_strength": _cell(None, None, "signed", True),
        },
        "defense": {
            "epa": _cell("rush_def_adj", None, "epa", False),
            "success_rate": _cell(None, None, "signed", False),
            "schedule_strength": _cell(None, None, "signed", True),
        },
    },
    {
        "id": "early_down",
        "label": "Early downs",
        "adjusted": False,
        "note": _RATE_NOTE,
        "offense": {
            "epa": _cell("early_epa_season", "early_epa_season_pct", "epa", True, "better", "the season rate"),
            "success_rate": _cell("early_sr_season", None, "share", True),
            "schedule_strength": _cell(None, None, "signed", True),
        },
        "defense": {
            "epa": _cell(None, None, "epa", False),
            "success_rate": _cell(None, None, "share", False),
            "schedule_strength": _cell(None, None, "signed", True),
        },
    },
    {
        "id": "passing_down",
        "label": "Passing downs",
        "adjusted": False,
        "note": _RATE_NOTE,
        "offense": {
            "epa": _cell(
                "passing_epa_season", "passing_epa_season_pct", "epa", True, "better", "the season rate",
            ),
            "success_rate": _cell("passing_sr_season", None, "share", True),
            "schedule_strength": _cell(None, None, "signed", True),
        },
        "defense": {
            "epa": _cell(None, None, "epa", False),
            "success_rate": _cell(None, None, "share", False),
            "schedule_strength": _cell(None, None, "signed", True),
        },
    },
)

_CHART = {
    "offense": ("epa_play_last4", "epa_play_season"),
    "defense": ("epa_allowed_last4", "epa_allowed_season"),
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


def _empty_rolling(reason: str | None = None) -> dict[str, Any]:
    return {
        "metric": "epa_play",
        "season_average": None,
        "last4_average": None,
        "games": [],
        "as_of_week": True,
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
    if frame is None or frame.empty:
        return frame if isinstance(frame, pd.DataFrame) else pd.DataFrame()
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


def _lookup(row: dict[str, Any] | None, column: str | None) -> Any:
    if row is None or not column or column not in row:
        return None
    value = row[column]
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    return value


def _metric_payload(row: dict[str, Any] | None, spec: _Cell, plays: int | None) -> dict[str, Any]:
    column, pct_col, display, higher, sense, pct_of = spec
    value = _finite(_lookup(row, column))
    percentile = _finite(_lookup(row, pct_col))
    return {
        "value": _num(value),
        "percentile": _num(percentile, 1),
        "higher_is_better": higher,
        "plays": plays,
        "rank_sense": sense,
        "display": display,
        "percentile_of": pct_of if percentile is not None else None,
    }


def _team_rows(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if frame is None or frame.empty or "team" not in frame.columns:
        return {}
    grouped: dict[str, dict[str, Any]] = {}
    for _, series in frame.iterrows():
        team = series.get("team")
        if not isinstance(team, str) or not team:
            continue
        grouped[canonical_team(team) or team] = series.to_dict()
    return grouped


def _location(value: Any) -> str | None:
    flag = _finite(value)
    if flag is None:
        return None
    return "home" if flag > 0 else "away"


def _label(week: float | None, opponent: str | None, location: str | None) -> str:
    where = "@" if location == "away" else "vs"
    week_text = f"Wk {int(week)} " if week is not None else ""
    return f"{week_text}{where} {opponent or '—'}".strip()


def _chart(
    games: list[dict[str, Any]],
    season_row: dict[str, Any] | None,
    side: str,
    empty_reason: str | None,
) -> dict[str, Any]:
    last4_col, season_col = _CHART[side]
    ordered = sorted(
        games,
        key=lambda row: (
            _finite(row.get("week")) is None,
            _finite(row.get("week")) or 0,
            str(row.get("game_id") or ""),
        ),
    )
    points: list[dict[str, Any]] = []
    for game in ordered:
        week = _finite(game.get("week"))
        opponent = game.get("opponent")
        if isinstance(opponent, str):
            opponent = canonical_team(opponent)
        location = _location(game.get("is_home"))
        last4 = _finite(game.get(last4_col))
        season_value = _finite(game.get(season_col))
        plays = _finite(game.get("n_plays_last4"))
        points.append({
            "week": int(week) if week is not None else None,
            "game_id": game.get("game_id"),
            "opponent": opponent,
            "location": location,
            "label": _label(week, opponent if isinstance(opponent, str) else None, location),
            "plays": int(plays) if plays is not None else 0,
            "value": _num(last4),
            "rolling4": _num(last4),
            "season_average": _num(season_value),
        })
    return {
        "metric": last4_col,
        "season_average": _num(_lookup(season_row, season_col)),
        "last4_average": _num(_lookup(season_row, last4_col)),
        "games": points,
        "as_of_week": True,
        "empty_reason": None if points else empty_reason,
    }


def _present_teams(
    season_frame: pd.DataFrame,
    game_frame: pd.DataFrame,
    *,
    placeholder: bool,
    empty_reason: str | None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    if placeholder or "epa_off_adj" not in getattr(season_frame, "columns", []):
        return {}, []
    if season_frame.empty and game_frame.empty:
        return {}, []

    by_season = _team_rows(season_frame)
    by_game: dict[str, list[dict[str, Any]]] = {}
    if not game_frame.empty and "team" in game_frame.columns:
        for _, series in game_frame.iterrows():
            team = series.get("team")
            if not isinstance(team, str) or not team:
                continue
            key = canonical_team(team) or team
            by_game.setdefault(key, []).append(series.to_dict())

    unavailable: list[dict[str, str]] = []
    if by_season:
        sample = next(iter(by_season.values()))
        missing = [
            spec[0]
            for split in _SPLITS
            if split["adjusted"]
            for side in ("offense", "defense")
            for spec in split[side].values()
            if spec[0] and spec[0] not in sample
        ]
        if missing:
            unavailable.append({
                "id": "feature_columns",
                "reason": (
                    "Some feature-contract columns are not in this season's table, "
                    "so those cells are blank: " + ", ".join(sorted(set(missing))) + "."
                ),
            })

    teams = sorted(set(by_season) | set(by_game))
    per_team: dict[str, dict[str, Any]] = {}
    for team in teams:
        row = by_season.get(team)
        plays = _finite(_lookup(row, "n_plays_season"))
        play_count = int(plays) if plays is not None else None
        splits = []
        for spec in _SPLITS:
            splits.append({
                "id": spec["id"],
                "label": spec["label"],
                "adjusted": spec["adjusted"],
                "approximated": False,
                "approximation_note": None,
                "note": spec["note"],
                "offense": {
                    key: _metric_payload(row, cell, play_count)
                    for key, cell in spec["offense"].items()
                },
                "defense": {
                    key: _metric_payload(row, cell, None)
                    for key, cell in spec["defense"].items()
                },
            })
        per_team[team] = {
            "splits": splits if row is not None else [],
            "rolling": {
                "offense": _chart(by_game.get(team, []), row, "offense", empty_reason),
                "defense": _chart(by_game.get(team, []), row, "defense", empty_reason),
            },
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
    if placeholder or "epa_off_adj" not in getattr(season_frame, "columns", []):
        placeholder = True
        public = _public_filters(filters, placeholder=True)
        unavailable.append({"id": "feature_defs", "reason": WAITING_REASON})
        chart_reason = "Game-by-game rates are omitted until features/nfl/defs.py is available."
        notes.append("The efficiency tab is waiting on the NFL feature contract. No rates were calculated for this page.")
    elif season_frame.empty and game_frame.empty:
        unavailable.append({
            "id": "feature_rows",
            "reason": f"The feature module returned no team rows for {season}.",
        })
    else:
        notes.append(
            "Headline EPA and success figures are the opponent-adjusted ratings "
            "from the NFL feature contract. A percentile ranks the matching season "
            "rate, and a higher percentile is better."
        )
        notes.append(
            "The chart is the last four team-games against the season-to-date rate, "
            "as of the start of each week. That week's game is not in the point."
        )
        if public.get("preseason_excluded"):
            notes.append("Preseason plays are dropped when the feed labels season type.")

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
        "team_season_rows": len(season_frame.index) if isinstance(season_frame, pd.DataFrame) else 0,
        "team_game_rows": len(game_frame.index) if isinstance(game_frame, pd.DataFrame) else 0,
        "team_season": _records(season_frame),
        "team_game": _records(game_frame),
        "teams": teams,
    }


def project_team(bundle: dict[str, Any], team_id: str, season: int) -> dict[str, Any]:
    """One team, in the shape the page renders."""
    team_id = canonical_team(team_id) or team_id
    rolling_reason = None
    if bundle.get("placeholder"):
        rolling_reason = "Game-by-game rates are omitted until features/nfl/defs.py is available."
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
    team = (bundle.get("teams") or {}).get(team_id)
    if team is None:
        if not bundle.get("placeholder"):
            teams = bundle.get("teams") or {}
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
    return f"nfl:{season}:defs"


def _ttl(season: int) -> int | None:
    if features.using_placeholder():
        return PLACEHOLDER_TTL
    if season < current_or_upcoming_season():
        return None
    return CACHE_TTL


def _no_pbp(season: int, *, reason: str, unavailable_id: str = "play_by_play") -> dict[str, Any]:
    return {
        "league": "NFL",
        "season": season,
        "source": features.source_name(),
        "placeholder": False,
        "cacheable": False,
        "filters": _public_filters(
            {"garbage_time_excluded": False, "description": reason},
            placeholder=True,
        ),
        "notes": [],
        "unavailable": [{"id": unavailable_id, "reason": reason}],
        "team_season_rows": 0,
        "team_game_rows": 0,
        "team_season": [],
        "team_game": [],
        "teams": {},
        "error": reason,
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
            return _no_pbp(season, reason="Play-by-play for this season is not in the data pipeline.")
    try:
        game_frame, season_frame = features.build(plays)
    except Exception as exc:  # noqa: BLE001 — a bad feature build must not 500 the page
        log.warning("team_features_failed", season=season, error=str(exc)[:200])
        return _no_pbp(
            season,
            reason="The feature module failed while building team tables, so the rates are omitted.",
            unavailable_id="feature_defs",
        )
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

    An empty download is not cached. A placeholder result uses a short TTL.
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
    """Worker job: refresh the live season and warm the latest completed one."""
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
