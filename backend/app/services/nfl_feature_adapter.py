"""Load ``features.nfl.defs`` or the one-file placeholder.

Callers pass a plays frame in and get team-game and team-season frames back.
Market and odds columns are dropped at this boundary so they never land in
a feature table, whichever module produced them.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from . import nfl_feature_placeholder as placeholder

# backend/app/services/<this file> -> repo root, where features/nfl/defs.py will live.
_REPO_ROOT = Path(__file__).resolve().parents[3]

# Substrings the feature tables are not allowed to carry. Matched on the
# column name, case-insensitive. "price" is the odds price, not a stat.
_MARKET_TOKENS = ("spread", "moneyline", "odds", "total_line", "price", "opener")

_cached: Any = None
_override: Any = None


def reset() -> None:
    """Drop the cached import and any test override."""
    global _cached, _override
    _cached = None
    _override = None


def use_module(module: Any) -> None:
    """Pin the active feature module. Tests and the future defs import use this."""
    global _override
    _override = module


def _import_defs() -> Any:
    root = str(_REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    return importlib.import_module("features.nfl.defs")


def module() -> Any:
    """The feature module currently in use."""
    global _cached
    if _override is not None:
        return _override
    if _cached is None:
        try:
            _cached = _import_defs()
        except ImportError:
            _cached = placeholder
    return _cached


def using_placeholder() -> bool:
    mod = module()
    if getattr(mod, "SOURCE", None) == "placeholder":
        return True
    return str(getattr(mod, "__name__", "")).endswith("nfl_feature_placeholder")


def source_name() -> str:
    mod = module()
    named = getattr(mod, "SOURCE", None)
    if named:
        return str(named)
    return str(getattr(mod, "__name__", "features.nfl.defs"))


def contract() -> dict[str, dict[str, str]]:
    raw = getattr(module(), "CONTRACT", None)
    if isinstance(raw, dict):
        return raw
    return {}


def bindings() -> dict[str, Any]:
    found = getattr(module(), "BINDINGS", None)
    if isinstance(found, dict) and found.get("season") and found.get("game"):
        return found
    return placeholder.BINDINGS


def filters() -> dict[str, Any]:
    """Filter declaration from the module. Never invents a garbage-time drop."""
    raw = getattr(module(), "FILTERS", None)
    if using_placeholder() or not isinstance(raw, dict):
        base = dict(placeholder.FILTERS)
        if not using_placeholder():
            base["implemented"] = True
            base["garbage_time_excluded"] = False
            base["description"] = (
                "features/nfl/defs.py does not declare a garbage-time filter, "
                "so this page does not claim one was applied."
            )
        return base
    return {
        "implemented": bool(raw.get("implemented", True)),
        "garbage_time_excluded": bool(raw.get("garbage_time_excluded", False)),
        "wp_low": raw.get("wp_low", placeholder.FILTERS["wp_low"]),
        "wp_high": raw.get("wp_high", placeholder.FILTERS["wp_high"]),
        "description": raw.get("description") or "",
        "plays_kept": raw.get("plays_kept"),
        "plays_excluded": raw.get("plays_excluded"),
        "preseason_excluded": bool(raw.get("preseason_excluded", False)),
    }


def market_columns(columns: Any) -> list[str]:
    found: list[str] = []
    for name in columns:
        lowered = str(name).lower()
        if any(token in lowered for token in _MARKET_TOKENS):
            found.append(str(name))
    return found


def drop_market(frame: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or frame.empty and len(frame.columns) == 0:
        return frame if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    bad = market_columns(frame.columns)
    if not bad:
        return frame
    return frame.drop(columns=bad)


def _call(name: str, plays: pd.DataFrame) -> pd.DataFrame:
    fn = getattr(module(), name, None)
    if fn is None:
        return pd.DataFrame()
    out = fn(plays)
    if not isinstance(out, pd.DataFrame):
        return pd.DataFrame()
    return drop_market(out)


def team_game(plays: pd.DataFrame) -> pd.DataFrame:
    return _call("team_game", plays)


def team_season(plays: pd.DataFrame) -> pd.DataFrame:
    return _call("team_season", plays)
