"""QA harness for the market-blind v6 core.

``hist`` and ``target`` may carry only team, date and score fields. Ratings
and the frozen coefficients live in this folder. They were built by
``backend/scripts/backtest_v6_blind.py``; see ``priors/README.md`` for the
source and the SHA-256 of each file.

``predict`` returns the home margin (home score minus away score) and the
total. It joins the precomputed week on home, away and date. It does not
read a spread, a total, a moneyline, or a score.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

_DIR = Path(__file__).resolve().parent
FEATURES = (
    "pass_comp",
    "rush_comp",
    "sr_comp",
    "hfa",
    "pass_sum",
    "rush_sum",
    "sr_sum",
)

_ALLOWED = {
    "home_team", "away_team", "team", "date", "gameday",
    "home_score", "away_score", "score",
    "season", "week", "game_id", "neutral", "location", "game_type",
}

_BANNED_TOKENS = (
    "spread", "moneyline", "kalshi", "adp", "implied", "mkt", "odds",
    "price", "opener", "closing", "total_line",
)


def _reject(frame: pd.DataFrame, name: str) -> None:
    extra = [c for c in frame.columns if c not in _ALLOWED]
    banned = [
        c for c in frame.columns
        if any(tok in str(c).lower() for tok in _BANNED_TOKENS)
    ]
    if extra or banned:
        raise ValueError(f"{name} carries columns the harness does not accept: {sorted(set(extra) | set(banned))}")


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
    _reject_features(features)
    features = features.copy()
    features["gameday"] = pd.to_datetime(features["gameday"], errors="coerce").dt.strftime("%Y-%m-%d")
    return features, coefs


def _reject_features(features: pd.DataFrame) -> None:
    banned = [
        c for c in features.columns
        if any(tok in str(c).lower() for tok in _BANNED_TOKENS)
    ]
    if banned:
        raise ValueError(f"prior file has market columns: {banned}")


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
    """Predicted home margin and total for each row of ``target``.

    ``hist`` is accepted because the harness contract requires it. The ratings
    were already walked forward into ``priors/week_features.csv``, so this
    function does not refit on ``hist`` and does not read scores from it.
    """
    if not isinstance(hist, pd.DataFrame) or not isinstance(target, pd.DataFrame):
        raise TypeError("hist and target must be DataFrames")
    _reject(hist, "hist")
    _reject(target, "target")
    features, coefs = _load()
    by_season = coefs["by_season"]
    frozen_m, frozen_t = coefs["margin"], coefs["total"]
    lookup = features.set_index(["home_team", "away_team", "gameday"], drop=False)
    dates = _date_key(target)
    margins = []
    totals = []
    for i, row in target.reset_index(drop=True).iterrows():
        key = (row["home_team"], row["away_team"], dates.iloc[i])
        if key not in lookup.index:
            margins.append(float("nan"))
            totals.append(float("nan"))
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
    return pd.DataFrame({"margin": margins, "total": totals}, index=target.index)
