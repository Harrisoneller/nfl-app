"""Look up a frozen v6-blind fair for a scheduled game.

The numbers are the harness priors (walk-forward team units, no market
column). A game that is not in the snapshot has no v6 fair; the caller keeps
the market-blind v5 fallback. This module does not publish picks.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[3]
_FEATURES = _ROOT / "backtest" / "harness_model" / "priors" / "week_features.csv"
_COEFS = _ROOT / "backtest" / "harness_model" / "coefs.json"


@lru_cache(maxsize=1)
def _tables() -> tuple[pd.DataFrame, dict] | None:
    if not _FEATURES.exists() or not _COEFS.exists():
        return None
    features = pd.read_csv(_FEATURES)
    features["gameday"] = pd.to_datetime(features["gameday"], errors="coerce").dt.strftime("%Y-%m-%d")
    coefs = json.loads(_COEFS.read_text())
    return features, coefs


def prediction_for(home: str, away: str, gameday: str) -> dict | None:
    """Home margin and total for one game, or None when the snapshot has no row."""
    loaded = _tables()
    if loaded is None or not home or not away or not gameday:
        return None
    features, coefs = loaded
    day = str(gameday)[:10]
    hit = features[
        (features["home_team"] == home)
        & (features["away_team"] == away)
        & (features["gameday"] == day)
    ]
    if len(hit) == 0:
        return None
    row = hit.iloc[0]
    season = str(int(row["season"]))
    spec = coefs["by_season"].get(season) or {"margin": coefs["margin"], "total": coefs["total"]}
    margin_c, total_c = spec["margin"], spec["total"]
    margin = (
        margin_c["pass"] * float(row["pass_comp"])
        + margin_c["rush"] * float(row["rush_comp"])
        + margin_c["success"] * float(row["sr_comp"])
        + margin_c["hfa"] * float(row["hfa"])
    )
    total = (
        total_c["const"]
        + total_c["pass"] * float(row["pass_sum"])
        + total_c["rush"] * float(row["rush_sum"])
        + total_c["success"] * float(row["sr_sum"])
    )
    parts = {
        "pass": round(margin_c["pass"] * float(row["pass_comp"]), 2),
        "rush": round(margin_c["rush"] * float(row["rush_comp"]), 2),
        "success": round(margin_c["success"] * float(row["sr_comp"]), 2),
        "hfa": round(margin_c["hfa"] * float(row["hfa"]), 2),
    }
    return {
        "model_version": "qb-ridge-v6-blind",
        "blind_core": True,
        "margin": float(margin),
        "total": float(total),
        "margin_parts": parts,
        "total_parts": {},
        "features": {
            "pass_comp": round(float(row["pass_comp"]), 4),
            "rush_comp": round(float(row["rush_comp"]), 4),
            "sr_comp": round(float(row["sr_comp"]), 4),
        },
        "uses_market_prior": False,
        "qb_change": {"home": False, "away": False},
    }
