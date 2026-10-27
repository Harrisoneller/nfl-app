"""Load ``features.nfl.defs`` and pass it only its play-column allowlist.

``build_features(plays)`` returns ``(team_game, team_season)``. The module is
part of the backend install (``features*`` in pyproject). A missing import is
an error. This adapter does not substitute the placeholder, and it does not
hand ``build_features`` a column that is outside ``PBP_INPUT_COLUMNS``.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from ..logging_config import get_logger
from . import nfl_feature_placeholder as placeholder

log = get_logger(__name__)


class FeatureDefsUnavailable(RuntimeError):
    """``features.nfl.defs`` is not importable. Ratings must not be invented."""

_cached: Any = None
_override: Any = None


def reset() -> None:
    """Drop the cached import and any test override."""
    global _cached, _override
    _cached = None
    _override = None


def use_module(module: Any) -> None:
    """Pin the active feature module. Tests use this; production uses the import."""
    global _override
    _override = module


def _import_defs() -> Any:
    """Import the installed ``features.nfl.defs`` module.

    The backend pyproject packages ``features*``, so this is a normal import.
    """
    try:
        from features.nfl import defs
    except ImportError as exc:
        log.error("feature_defs_import_failed", error=str(exc))
        raise FeatureDefsUnavailable(
            "features.nfl.defs could not be imported. "
            "Team efficiency ratings are unavailable until that package is installed."
        ) from exc
    return defs


def module() -> Any:
    """The feature module currently in use. Raises if it cannot be imported."""
    global _cached
    if _override is not None:
        return _override
    if _cached is None:
        try:
            _cached = _import_defs()
        except FeatureDefsUnavailable:
            raise
        except ImportError as exc:
            log.error("feature_defs_import_failed", error=str(exc))
            raise FeatureDefsUnavailable(
                "features.nfl.defs could not be imported. "
                "Team efficiency ratings are unavailable until that package is installed."
            ) from exc
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


def contract() -> dict[str, Any]:
    raw = getattr(module(), "CONTRACT", None)
    return raw if isinstance(raw, dict) else {}


def is_market_column(name: str) -> bool:
    """Match the feature module's market ban, plus price and opener.

    ``total_home_score`` is a final score, not a betting total, and is kept.
    """
    key = str(name).lower()
    if any(token in key for token in ("spread", "moneyline", "odds", "price", "opener")):
        return True
    return key in {"total", "total_line"} or key.endswith("_total") or "total_line" in key


def market_columns(columns: Any) -> list[str]:
    return [str(name) for name in columns if is_market_column(str(name))]


def input_allowlist() -> set[str]:
    """Columns ``build_features`` accepts. A test double may not declare them."""
    cols = getattr(module(), "PBP_INPUT_COLUMNS", None)
    if not cols:
        cols = _import_defs().PBP_INPUT_COLUMNS
    return {str(col) for col in cols}


def project_inputs(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep only allowlisted play columns, in their original order."""
    if not isinstance(frame, pd.DataFrame):
        return pd.DataFrame()
    allowed = input_allowlist()
    keep = [col for col in frame.columns if str(col) in allowed]
    return frame.loc[:, keep].copy()


def drop_market(frame: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        return pd.DataFrame()
    bad = market_columns(frame.columns)
    if not bad:
        return frame
    return frame.drop(columns=bad)


def filters() -> dict[str, Any]:
    """Garbage-time flag from the loaded module. The placeholder does not filter."""
    if using_placeholder() or not hasattr(module(), "build_features"):
        return dict(placeholder.FILTERS)
    mod = module()
    lo = getattr(mod, "WP_LO", None)
    hi = getattr(mod, "WP_HI", None)
    if lo is None or hi is None:
        return {
            "implemented": True,
            "garbage_time_excluded": False,
            "wp_low": 0.10,
            "wp_high": 0.90,
            "description": (
                "The feature module did not declare a win-probability band, "
                "so this page does not claim garbage time was excluded."
            ),
            "plays_kept": None,
            "plays_excluded": None,
            "preseason_excluded": False,
        }
    low, high = float(lo), float(hi)
    return {
        "implemented": True,
        "garbage_time_excluded": True,
        "wp_low": low,
        "wp_high": high,
        "description": (
            "Garbage time is excluded. Only plays with win probability between "
            f"{low:.0%} and {high:.0%} are counted."
        ),
        "plays_kept": None,
        "plays_excluded": None,
        "preseason_excluded": True,
    }


def _as_frame(value: Any) -> pd.DataFrame:
    if isinstance(value, pd.DataFrame):
        return drop_market(value)
    return pd.DataFrame()


def build(plays: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Team-game and team-season frames.

    Only ``PBP_INPUT_COLUMNS`` are passed in. Market columns and any other
    extra play fields stay out, including ones the market guard would ignore.
    """
    clean = project_inputs(plays if isinstance(plays, pd.DataFrame) else pd.DataFrame())
    fn = getattr(module(), "build_features", None)
    if fn is None:
        return _as_frame(module().team_game(clean)), _as_frame(module().team_season(clean))
    game, season = fn(clean)
    return _as_frame(game), _as_frame(season)
