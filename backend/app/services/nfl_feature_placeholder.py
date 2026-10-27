"""Fallback when ``features.nfl.defs`` cannot be imported.

The live contract is ``features/nfl/defs.py`` (``build_features``). This module
is only the empty stand-in so a checkout without that file still omits rates
instead of inventing them. Do not add EPA, success-rate, or
opponent-adjustment math here, and do not add market or odds columns.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

SOURCE = "placeholder"

# The page may say garbage time was excluded only when the loaded module
# sets these. This stub does not filter plays.
FILTERS: dict[str, Any] = {
    "implemented": False,
    "garbage_time_excluded": False,
    "wp_low": 0.10,
    "wp_high": 0.90,
    "description": (
        "Garbage time is not excluded here. That filter belongs to "
        "features/nfl/defs.py, which is not loaded in this build."
    ),
}

SPLITS: tuple[str, ...] = ("overall", "pass", "rush", "early_down", "passing_down")

SPLIT_LABELS: dict[str, str] = {
    "overall": "All plays",
    "pass": "Pass",
    "rush": "Rush",
    "early_down": "Early downs",
    "passing_down": "Passing downs",
}

_DTYPES = {"float": "float64", "int": "int64", "string": "object"}


def _spec(dtype: str, unit: str, description: str) -> dict[str, str]:
    return {"dtype": dtype, "unit": unit, "description": description}


def _season_columns() -> dict[str, dict[str, str]]:
    """One row per team. Values are published by defs; this file only names them."""
    columns: dict[str, dict[str, str]] = {
        "season": _spec("int", "season", "NFL season the row describes."),
        "team": _spec("string", "team_id", "Canonical team abbreviation, one row per team."),
    }
    for split, label in SPLIT_LABELS.items():
        columns[f"off_{split}_epa"] = _spec(
            "float",
            "epa_per_play",
            f"Opponent-adjusted offensive EPA per play, {label}. "
            "Positive is better than a league-average offense. "
            "Garbage time is the feature module's responsibility, not the page's.",
        )
        columns[f"off_{split}_success"] = _spec(
            "float",
            "rate",
            f"Opponent-adjusted offensive success rate, {label}. "
            "Share of plays from 0 to 1, not a percentage.",
        )
        columns[f"off_{split}_plays"] = _spec(
            "int",
            "plays",
            f"Offensive plays in the {label} split after the feature module's filters.",
        )
        columns[f"off_{split}_schedule_strength"] = _spec(
            "float",
            "epa_per_play",
            f"Schedule strength for the {label} offensive split. "
            "Positive means a tougher slate than the league.",
        )
        columns[f"def_{split}_epa"] = _spec(
            "float",
            "epa_per_play",
            f"Opponent-adjusted EPA per play allowed, {label}. "
            "Lower is a better defense.",
        )
        columns[f"def_{split}_success"] = _spec(
            "float",
            "rate",
            f"Opponent-adjusted success rate allowed, {label}. Lower is better.",
        )
        columns[f"def_{split}_plays"] = _spec(
            "int",
            "plays",
            f"Defensive plays in the {label} split after the feature module's filters.",
        )
        columns[f"def_{split}_schedule_strength"] = _spec(
            "float",
            "epa_per_play",
            f"Schedule strength for the {label} defensive split. "
            "Positive means the offenses faced were tougher than average.",
        )
    return columns


def _game_columns() -> dict[str, dict[str, str]]:
    """One row per team per game. Chart input only; not a second adjustment."""
    return {
        "season": _spec("int", "season", "NFL season of the game."),
        "week": _spec("int", "week", "NFL week number."),
        "game_id": _spec("string", "game_id", "Stable game identifier from the play feed."),
        "team": _spec("string", "team_id", "Canonical team abbreviation."),
        "opponent": _spec("string", "team_id", "Canonical opponent abbreviation."),
        "location": _spec("string", "home_away", "home or away for this team."),
        "off_epa": _spec(
            "float",
            "epa_per_play",
            "Offensive EPA per play in this game, as published by the feature "
            "module. The page averages these rows; it does not recompute EPA "
            "or opponent adjustment.",
        ),
        "def_epa": _spec(
            "float",
            "epa_per_play",
            "EPA per play allowed in this game, as published by the feature module.",
        ),
        "off_plays": _spec("int", "plays", "Offensive plays in this game in the feature table."),
        "def_plays": _spec("int", "plays", "Defensive plays in this game in the feature table."),
    }


CONTRACT: dict[str, dict[str, str]] = {**_season_columns(), **_game_columns()}

TEAM_SEASON_COLUMNS: tuple[str, ...] = tuple(_season_columns())
TEAM_GAME_COLUMNS: tuple[str, ...] = tuple(_game_columns())


def _side(prefix: str) -> dict[str, str]:
    return {
        "epa": f"{prefix}_epa",
        "success_rate": f"{prefix}_success",
        "plays": f"{prefix}_plays",
        "schedule_strength": f"{prefix}_schedule_strength",
    }


def _bindings() -> dict[str, Any]:
    splits = {
        split: {
            "label": SPLIT_LABELS[split],
            "offense": _side(f"off_{split}"),
            "defense": _side(f"def_{split}"),
        }
        for split in SPLITS
    }
    return {
        "season": {"team": "team", "splits": splits},
        "game": {
            "team": "team",
            "opponent": "opponent",
            "week": "week",
            "game_id": "game_id",
            "location": "location",
            "is_home": "is_home",
            "offense_epa": "off_epa",
            "defense_epa": "def_epa",
            "offense_plays": "off_plays",
            "defense_plays": "def_plays",
        },
    }


# Column names the page reads. A landed defs module can replace this with its
# own BINDINGS; otherwise the adapter keeps using these names.
BINDINGS: dict[str, Any] = _bindings()


def _empty(columns: tuple[str, ...] | list[str]) -> pd.DataFrame:
    data: dict[str, pd.Series] = {}
    for name in columns:
        dtype = _DTYPES.get(CONTRACT.get(name, {}).get("dtype", "string"), "object")
        data[name] = pd.Series(dtype=dtype)
    return pd.DataFrame(data)


def team_game(plays: pd.DataFrame) -> pd.DataFrame:
    """Team-game frame. Plays are accepted and ignored until defs.py exists."""
    if not isinstance(plays, pd.DataFrame):
        raise TypeError("team_game expects a plays DataFrame")
    return _empty(TEAM_GAME_COLUMNS)


def team_season(plays: pd.DataFrame) -> pd.DataFrame:
    """Team-season frame. Plays are accepted and ignored until defs.py exists."""
    if not isinstance(plays, pd.DataFrame):
        raise TypeError("team_season expects a plays DataFrame")
    return _empty(TEAM_SEASON_COLUMNS)
