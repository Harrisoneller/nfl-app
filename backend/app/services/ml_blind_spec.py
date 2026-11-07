"""Locked constants for the market-blind NFL ML retrain.

The human contract is ``research/nfl-ml-blind/PREREG.md``.
``research/nfl-ml-blind/TRAIN_SPEC.json`` must match this module.
Do not change a value after the holdout has been scored.
"""
from __future__ import annotations

from collections.abc import Iterable

SEED = 20261026
SIGMA = 13.5
RESULT_LAG_DAYS = 2
TRAIN_FIRST_SEASON = 2012
TEST_SEASON_FIRST = 2016
TEST_SEASON_LAST = 2025
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_PERCENTILES = (5.0, 95.0)
ATS_NONINFERIORITY = -0.005

EPA_WINDOW = 4
EPA_HALF_LIFE_GAMES = 3.5
REST_DEFAULT_DAYS = 7.0
REST_CLIP = (3.0, 21.0)
SHORT_WEEK_MAX_DAYS = 6.0

ELO_K = 20.0
ELO_HOME_FIELD = 55.0
ELO_INITIAL = 1500.0
ELO_SEASON_CARRY = 0.75

PLATT_EPOCHS = 300
PLATT_LEARNING_RATE = 0.08
PLATT_MIN_ROWS = 20

WEIGHT_HALF_LIFE = 6.0
WEIGHT_SHORT_WEEK_HOME = 0.15
WEIGHT_SHORT_WEEK_AWAY = 0.10
WEIGHT_WEATHER_IS_BAD = 0.08
WEIGHT_QB_CHANGE_HOME = 0.10
WEIGHT_QB_CHANGE_AWAY = 0.10

OL_INJURY_EARLY = 0.25
OL_INJURY_LATER = 0.1
OL_INJURY_EARLY_WEEKS = 2

FEATURE_COLUMNS: tuple[str, ...] = (
    "home_elo",
    "away_elo",
    "home_off_epa",
    "away_off_epa",
    "home_def_epa",
    "away_def_epa",
    "home_off_epa_recency",
    "away_off_epa_recency",
    "home_def_epa_recency",
    "away_def_epa_recency",
    "rest_days_home",
    "rest_days_away",
    "short_week_home",
    "short_week_away",
    "away_travel_proxy",
    "weather_class_code",
    "weather_is_bad",
    "qb_change_proxy_home",
    "qb_change_proxy_away",
    "offense_continuity_home",
    "offense_continuity_away",
    "ol_injury_proxy_home",
    "ol_injury_proxy_away",
    "is_division_game",
    "week_number",
)

_TREE = {
    "n_estimators": 260,
    "max_depth": 4,
    "learning_rate": 0.05,
    "subsample": 0.85,
    "colsample_bytree": 0.85,
    "min_child_weight": 1,
    "reg_lambda": 1.0,
    "tree_method": "hist",
    "n_jobs": 1,
    "random_state": SEED,
}

SPREAD_MODEL_PARAMS = {
    **_TREE,
    "objective": "reg:squarederror",
}

WIN_MODEL_PARAMS = {
    **_TREE,
    "objective": "binary:logistic",
    "eval_metric": "logloss",
}

MARKET_TOKENS: tuple[str, ...] = (
    "spread",
    "moneyline",
    "odds",
    "price",
    "market",
    "vegas",
    "implied",
    "closing",
    "opener",
    "total_line",
    "juice",
    "vig",
    "clv",
)

HISTORY_GAME_TYPES = frozenset({"REG", "POST"})
GRADED_GAME_TYPE = "REG"

INCUMBENT_MODEL_VERSION = "qb-ridge-v5"
INCUMBENT_COMMIT = "389498b7a060c8e62619b3369515f14ac00b72ff"
INCUMBENT_DEPLOYMENT = "2d608399-7172-4007-874e-17c8ed19bfa5"
PREREG_SHA = "dbbe0fbcf3f8345cc1e5d4f98ca9fc0e320c3604"
QA_LOCK_ENV = "NFL_ML_BLIND_EVAL_CONFIRM"
QA_LOCK_VALUE = "QA-LOCK"


def is_market_name(name: str) -> bool:
    folded = str(name).strip().lower()
    return any(token in folded for token in MARKET_TOKENS)


def assert_no_market_columns(columns: Iterable[str]) -> None:
    """Raise if any column name carries a line, price, or market token."""
    bad = [str(col) for col in columns if is_market_name(str(col))]
    if bad:
        raise ValueError(f"market columns are not allowed: {bad}")


def assert_feature_allowlist(columns: Iterable[str]) -> None:
    """Raise unless every column is on the feature allowlist and non-market."""
    assert_no_market_columns(FEATURE_COLUMNS)
    requested = [str(col) for col in columns]
    assert_no_market_columns(requested)
    unknown = [col for col in requested if col not in FEATURE_COLUMNS]
    if unknown:
        raise ValueError(f"columns are not on the feature allowlist: {unknown}")


def test_seasons() -> list[int]:
    return list(range(TEST_SEASON_FIRST, TEST_SEASON_LAST + 1))
