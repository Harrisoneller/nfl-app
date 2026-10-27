"""QA harness for the market-blind v6 core.

``predict(hist, target)`` follows the harness contract:

* ``hist`` and ``target`` are schedule frames. Standard columns are accepted
  and ignored unless they identify the game: ``game_id``, ``season``, ``week``,
  ``date`` or ``gameday``, ``home_team``, ``away_team``, ``home_score``,
  ``away_score``, plus schedule context (``game_type``, ``location``,
  ``neutral_site``, rest, roof, surface, stadium, coaches, quarterback ids).
  Grading columns such as ``spread_line``, ``total_line`` and moneylines may
  sit on the frame. They are not read.
* One output row per input row, same order. Columns are ``game_id`` and
  ``pred_*`` (home margin, total, and the two team scores).
* Team codes ``LA``, ``OAK``, ``SD`` and ``STL`` map to the current franchise
  (``LAR``, ``LV``, ``LAC``, ``LAR``) before the prior join. A target row with
  no prior is logged and raised. Rows are not dropped.

``hist`` is part of the contract. Ratings were already walked forward into
``priors/week_features.csv``, so this function does not refit and does not
read scores. See ``priors/README.md`` for the file hashes.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

_DIR = Path(__file__).resolve().parent
log = logging.getLogger("v6_harness")

FEATURES = (
    "pass_comp",
    "rush_comp",
    "sr_comp",
    "hfa",
    "pass_sum",
    "rush_sum",
    "sr_sum",
)

# Historical abbreviations the priors store under the current franchise id.
_FRANCHISE = {
    "LA": "LAR",
    "OAK": "LV",
    "SD": "LAC",
    "STL": "LAR",
    "JAC": "JAX",
    "WSH": "WAS",
    "ARZ": "ARI",
    "HST": "HOU",
    "BLT": "BAL",
    "CLV": "CLE",
}

_OUTPUT_COLUMNS = (
    "game_id",
    "pred_margin",
    "pred_total",
    "pred_home_score",
    "pred_away_score",
)

_BANNED_TOKENS = (
    "spread", "moneyline", "kalshi", "adp", "implied", "mkt", "odds",
    "price", "opener", "closing", "total_line",
)


def _franchise(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    token = value.strip().upper()
    return _FRANCHISE.get(token, token)


def _date_key(frame: pd.DataFrame) -> pd.Series:
    if "date" in frame.columns:
        raw = frame["date"]
    elif "gameday" in frame.columns:
        raw = frame["gameday"]
    else:
        raise ValueError("target needs a date or gameday column")
    return pd.to_datetime(raw, errors="coerce").dt.strftime("%Y-%m-%d")


def _load():
    features = pd.read_csv(_DIR / "priors" / "week_features.csv")
    coefs = json.loads((_DIR / "coefs.json").read_text())
    banned = [
        c for c in features.columns
        if any(tok in str(c).lower() for tok in _BANNED_TOKENS)
    ]
    if banned:
        raise ValueError(f"prior file has market columns: {banned}")
    features = features.copy()
    features["home_team"] = features["home_team"].map(_franchise)
    features["away_team"] = features["away_team"].map(_franchise)
    features["gameday"] = pd.to_datetime(features["gameday"], errors="coerce").dt.strftime("%Y-%m-%d")
    return features, coefs


def _apply(row: pd.Series, margin: dict, total: dict) -> tuple[float, float]:
    m = (
        margin["pass"] * float(row["pass_comp"])
        + margin["rush"] * float(row["rush_comp"])
        + margin["success"] * float(row["sr_comp"])
        + margin["hfa"] * float(row["hfa"])
    )
    t = (
        total["const"]
        + total["pass"] * float(row["pass_sum"])
        + total["rush"] * float(row["rush_sum"])
        + total["success"] * float(row["sr_sum"])
    )
    return m, t


def predict(hist: pd.DataFrame, target: pd.DataFrame) -> pd.DataFrame:
    """One prediction per target row. ``hist`` is accepted and not used to refit."""
    if not isinstance(hist, pd.DataFrame) or not isinstance(target, pd.DataFrame):
        raise TypeError("hist and target must be DataFrames")
    if "home_team" not in target.columns or "away_team" not in target.columns:
        raise ValueError("target needs home_team and away_team")
    if "game_id" not in target.columns:
        raise ValueError("target needs game_id")
    features, coefs = _load()
    by_season = coefs["by_season"]
    frozen_m, frozen_t = coefs["margin"], coefs["total"]
    lookup = features.set_index(["home_team", "away_team", "gameday"], drop=False)
    dates = _date_key(target)
    home = target["home_team"].map(_franchise)
    away = target["away_team"].map(_franchise)
    margins: list[float] = []
    totals: list[float] = []
    missing: list[str] = []
    ordered = target.reset_index(drop=True)
    for i, row in ordered.iterrows():
        key = (home.iloc[i], away.iloc[i], dates.iloc[i])
        game_id = row["game_id"]
        if key not in lookup.index or pd.isna(key[0]) or pd.isna(key[1]) or pd.isna(key[2]):
            missing.append(str(game_id))
            continue
        feat = lookup.loc[key]
        if isinstance(feat, pd.DataFrame):
            feat = feat.iloc[0]
        season = str(int(feat["season"]))
        spec = by_season.get(season)
        if spec is None:
            margin_c, total_c = frozen_m, frozen_t
        else:
            margin_c, total_c = spec["margin"], spec["total"]
        m, t = _apply(feat, margin_c, total_c)
        margins.append(m)
        totals.append(t)
    if missing:
        sample = ", ".join(missing[:12])
        log.error(
            "harness refused to drop %s target games with no prior row: %s",
            len(missing), sample,
        )
        raise RuntimeError(
            f"{len(missing)} target games have no prior row after franchise mapping: {sample}"
        )
    margin_s = pd.Series(margins, dtype=float)
    total_s = pd.Series(totals, dtype=float)
    out = pd.DataFrame({
        "game_id": ordered["game_id"].to_numpy(),
        "pred_margin": margin_s.to_numpy(),
        "pred_total": total_s.to_numpy(),
        "pred_home_score": ((total_s + margin_s) / 2.0).to_numpy(),
        "pred_away_score": ((total_s - margin_s) / 2.0).to_numpy(),
    })
    if len(out) != len(target):
        log.error("harness row count %s != target %s", len(out), len(target))
        raise RuntimeError(f"harness returned {len(out)} rows for {len(target)} target games")
    out.index = target.index
    return out[list(_OUTPUT_COLUMNS)]
