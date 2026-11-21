"""No-market weekly v5 replay.

The pieces below are the locked path. They are not a season runner, and the
tests call them on synthetic rows only. Ratings use ``fit_ratings`` and
``build_qb_book`` with the frozen ``DEFAULT_PARAMS``. Stage-2 coefficients
are refit here. Shipped coefficients, ``build_week_model``, ``market_ratings``,
and ``scripts.backtest_v5`` are not used.
"""

from __future__ import annotations

import hashlib
import json
import math

import numpy as np
import pandas as pd

from app.services.team_ratings_v5 import DEFAULT_PARAMS, build_qb_book, fit_ratings

from . import sim_config as cfg
from .calendar import SCREEN_CUTOFF, eastern_kickoff
from .market import assert_no_market_columns, strip_market_columns

# Canonical hash of DEFAULT_PARAMS. sort_keys and compact separators are part
# of the pin. The market keys are in the dict and are not read by this module.
PARAMS_SHA256 = hashlib.sha256(
    json.dumps(DEFAULT_PARAMS, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()

MARGIN_COLUMNS = ("epa_d", "pts_d", "st_d", "qb_d", "hfa")
TOTAL_COLUMNS = ("pts_s", "epa_s", "env2", "plays_s", "qb_s")
MIN_COMPLETED_REG = 32
PMF_LO = -80
PMF_HI = 80
BOOTSTRAP_SEED = 20261115
BOOTSTRAP_REPS = 999
WEBB_VALUES = (
    -math.sqrt(1.5),
    -1.0,
    -math.sqrt(0.5),
    math.sqrt(0.5),
    1.0,
    math.sqrt(1.5),
)
# Equal probability 1/6. The last entry absorbs the floating-point remainder
# so the vector sums to 1, which is what the generator requires.
_WEBB_PROBS = np.full(6, 1.0 / 6.0)
_WEBB_PROBS[-1] = 1.0 - float(_WEBB_PROBS[:-1].sum())
WEBB_PROBS = tuple(float(value) for value in _WEBB_PROBS)
DROP_RATE_LIMIT = 0.02


def frozen_params() -> dict[str, float]:
    """A copy of the frozen parameter dict. Callers cannot replace it."""
    return dict(DEFAULT_PARAMS)


def strip_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Drop market columns, then refuse the frame if any remain."""
    cleaned, _removed = strip_market_columns(frame)
    assert_no_market_columns(cleaned, "v5 replay")
    return cleaned


def rows_before(frame: pd.DataFrame, kickoff_before: object | None, *, screen: bool = True) -> pd.DataFrame:
    """Keep rows whose Eastern kickoff is strictly before the week cutoff.

    ``kickoff_before`` is the scoring week's first kickoff. With ``screen``
    left on, rows on or after 2027-01-01 ET are removed. The totals freeze
    turns the screen off so a 2027 week can see earlier 2027 kickoffs. A
    frame with no kickoff column is returned stripped.
    """
    cleaned = strip_frame(frame)
    if "kickoff_et" not in cleaned.columns:
        return cleaned
    stamps = [eastern_kickoff(value) for value in cleaned["kickoff_et"]]
    keep = []
    for stamp in stamps:
        if stamp is None or (screen and stamp >= SCREEN_CUTOFF):
            keep.append(False)
            continue
        keep.append(kickoff_before is None or stamp < eastern_kickoff(kickoff_before))
    return cleaned.loc[np.asarray(keep, dtype=bool)].copy()


def ols_is_identified(n_games: int) -> bool:
    """True when a week has the pinned 2015-or-later training floor for a fit."""
    return int(n_games) >= MIN_COMPLETED_REG


def fit_ols(features: np.ndarray, target: np.ndarray) -> np.ndarray | None:
    """Ordinary least squares. A non-finite input or a failed solve returns None."""
    x = np.asarray(features, dtype=float)
    y = np.asarray(target, dtype=float)
    if x.ndim != 2 or len(x) == 0 or not np.isfinite(x).all() or not np.isfinite(y).all():
        return None
    coef, *_rest = np.linalg.lstsq(x, y, rcond=None)
    if not np.isfinite(coef).all():
        return None
    return coef


def predict_ols(features: np.ndarray, coef: np.ndarray) -> np.ndarray | None:
    x = np.asarray(features, dtype=float)
    if not np.isfinite(x).all():
        return None
    out = x @ coef
    if not np.isfinite(out).all():
        return None
    return out


def _design(frame: pd.DataFrame, columns: tuple[str, ...], intercept: bool) -> np.ndarray:
    parts = [frame[column].to_numpy(dtype=float) for column in columns]
    if intercept:
        parts.append(np.ones(len(frame)))
    return np.column_stack(parts)


def replay_week(train: pd.DataFrame, test: pd.DataFrame) -> dict:
    """Refit margin and total on ``train`` and apply them to ``test``.

    Returns ``ok`` False when the training floor is missed, a market column
    is present, or either fit is non-finite. No shipped coefficient is read.
    """
    train = strip_frame(train)
    test = strip_frame(test)
    if not ols_is_identified(len(train)):
        return {"ok": False, "reason": "underidentified", "n": len(train)}
    needed = list(MARGIN_COLUMNS) + list(TOTAL_COLUMNS) + ["margin", "total"]
    test_needed = list(MARGIN_COLUMNS) + list(TOTAL_COLUMNS)
    if train[needed].isna().any().any() or test[test_needed].isna().any().any():
        return {"ok": False, "reason": "nan", "n": len(train)}
    margin_coef = fit_ols(_design(train, MARGIN_COLUMNS, intercept=False), train["margin"].to_numpy())
    total_coef = fit_ols(_design(train, TOTAL_COLUMNS, intercept=True), train["total"].to_numpy())
    if margin_coef is None or total_coef is None:
        return {"ok": False, "reason": "ols", "n": len(train)}
    margin = predict_ols(_design(test, MARGIN_COLUMNS, intercept=False), margin_coef)
    total = predict_ols(_design(test, TOTAL_COLUMNS, intercept=True), total_coef)
    if margin is None or total is None:
        return {"ok": False, "reason": "nan", "n": len(train)}
    return {
        "ok": True,
        "margin": margin,
        "total": total,
        "margin_coef": margin_coef,
        "total_coef": total_coef,
    }


def sample_sd(residuals: np.ndarray) -> float | None:
    """Sample standard deviation with divisor n − 1. None when n < 2."""
    values = np.asarray(residuals, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) < 2:
        return None
    return float(np.std(values, ddof=1))


def normal_integer_pmf(mean: float, sigma: float, lo: int = PMF_LO, hi: int = PMF_HI) -> np.ndarray | None:
    """Normal mass on each integer. Endpoints hold the tails. Sums to 1."""
    if not np.isfinite(mean) or not np.isfinite(sigma) or sigma <= 0:
        return None
    scale = sigma * math.sqrt(2.0)
    ks = np.arange(lo, hi + 1)
    upper = 0.5 * (1.0 + np.vectorize(math.erf)((ks + 0.5 - mean) / scale))
    lower = 0.5 * (1.0 + np.vectorize(math.erf)((ks - 0.5 - mean) / scale))
    mass = upper - lower
    mass[0] = upper[0]
    mass[-1] = 1.0 - lower[-1]
    total = float(mass.sum())
    if total <= 0 or not np.isfinite(total):
        return None
    return mass / total


def empirical_pmf(samples: np.ndarray, lo: int = PMF_LO, hi: int = PMF_HI) -> np.ndarray | None:
    """Integer frequencies. Mass outside the grid is piled on the endpoints."""
    values = np.asarray(samples, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) == 0:
        return None
    clipped = np.clip(np.rint(values).astype(int), lo, hi)
    counts = np.bincount(clipped - lo, minlength=hi - lo + 1).astype(float)
    return counts / counts.sum()


def discrete_crps(pmf: np.ndarray, actual: float, lo: int = PMF_LO) -> float | None:
    """Ranked probability score on an integer grid."""
    if pmf is None or not np.isfinite(actual):
        return None
    probs = np.asarray(pmf, dtype=float)
    if not np.isfinite(probs).all() or probs.sum() <= 0:
        return None
    cdf = np.cumsum(probs / probs.sum())
    ks = np.arange(lo, lo + len(probs) - 1)
    return float(np.sum((cdf[:-1] - (actual <= ks).astype(float)) ** 2))


def resolve_qb(depth_chart_qb: str | None, prior_starts: list[tuple[object, str]]) -> tuple[str | None, str]:
    """Depth-chart QB1, else the newest prior start strictly before kickoff.

    ``prior_starts`` is ``(kickoff, qb_id)`` for that team's earlier completed
    games, newest last or unsorted. The qb id is a starter id, not a passer id.
    """
    if isinstance(depth_chart_qb, str) and depth_chart_qb:
        return depth_chart_qb, "depth_chart"
    chosen: tuple[object, str] | None = None
    for kickoff, qb_id in prior_starts:
        if not isinstance(qb_id, str) or not qb_id:
            continue
        if chosen is None or eastern_kickoff(kickoff) > eastern_kickoff(chosen[0]):
            chosen = (kickoff, qb_id)
    if chosen is None:
        return None, "missing"
    return chosen[1], "prior_start"


def game_dropped(home_qb: str | None, away_qb: str | None, home_ok: bool, away_ok: bool) -> str | None:
    """A missing quarterback or a failed model drops the game from both."""
    if not home_qb or not away_qb:
        return "missing_qb"
    if not home_ok or not away_ok:
        return "model_failure"
    return None


def drop_rate_voids(n_dropped: int, n_completed: int) -> bool:
    """True when the drop rate is above the pinned 2% review line."""
    if n_completed <= 0:
        return True
    return (n_dropped / n_completed) > DROP_RATE_LIMIT


def sim_seed(game_id: str) -> int:
    """The 2018 dev seed: frozen SEED plus the first six sha256 hex digits."""
    digest = hashlib.sha256(str(game_id).encode()).hexdigest()
    return int(cfg.SEED) + int(digest[:6], 16)


def bootstrap_rng() -> np.random.Generator:
    """The only bootstrap generator. It is not the per-game sim stream."""
    return np.random.default_rng(BOOTSTRAP_SEED)


def webb_mean_interval(
    values: np.ndarray,
    clusters: np.ndarray,
    rng: np.random.Generator,
    reps: int = BOOTSTRAP_REPS,
) -> tuple[float, float, float] | None:
    """90% Webb wild-cluster interval for the mean. Synthetic inputs only in tests."""
    sample = np.asarray(values, dtype=float)
    labels = np.asarray(clusters)
    if len(sample) == 0 or not np.isfinite(sample).all():
        return None
    order: list = []
    for label in labels:
        if label not in order:
            order.append(label)
    center = float(sample.mean())
    shifted = sample - center
    boots = np.empty(reps)
    for draw in range(reps):
        weights = rng.choice(WEBB_VALUES, size=len(order), p=WEBB_PROBS)
        by_cluster = dict(zip(order, weights, strict=True))
        applied = np.array([by_cluster[label] for label in labels])
        boots[draw] = center + float(np.mean(shifted * applied))
    low, high = np.quantile(boots, [0.05, 0.95])
    return center, float(low), float(high)


def ratings_as_of(
    rows: pd.DataFrame,
    season: int,
    week: int,
    teams: list[str],
    kickoff_before: object | None,
    *,
    screen: bool = True,
):
    """Team ratings and the QB book. Market ratings are not built."""
    kept = rows_before(rows, kickoff_before, screen=screen)
    params = frozen_params()
    ratings = fit_ratings(kept, season, week, teams, params)
    book = build_qb_book(kept, season, week, teams, params)
    return ratings, book
