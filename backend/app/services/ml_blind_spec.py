"""Constants for the market-blind NFL ML lock candidate.

The contract is ``research/nfl-ml-blind/PREREG.md``.
``research/nfl-ml-blind/TRAIN_SPEC.json`` must match ``spec_document()``.
This module does not embed a commit SHA. QA identifies the lock commit from git.
"""
from __future__ import annotations

from collections.abc import Iterable

SEED = 20261026
SIGMA = 13.5
RESULT_LAG_DAYS = 2
TRAIN_FIRST_SEASON = 2012
HISTORY_SEASON = 2011
SELECTION_SEASONS = (2013, 2014, 2015)
FINAL_SEASON_FIRST = 2016
FINAL_SEASON_LAST = 2025
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_PERCENTILES = (5.0, 95.0)
# Candidate CRPS minus close CRPS. Cluster 95th percentile must be at or below this.
CLOSE_NI_UPPER = 0.25
# Candidate cover rate minus incumbent cover rate, in percentage points.
ATS_CLUSTER_LOWER_PP = -2.0
Z_MIN = 2.0
POWER_MIN = 0.5
MDE_CRPS = 0.25
# Standard-normal 95th percentile. The 90% interval's upper end.
Z_CRIT_95 = 1.6448536269514722

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
INCUMBENT_MODE = "walk_forward_stage2"
FROZEN_PROD_REPLAY = "descriptive_only"
INCUMBENT_COMMIT = "389498b7a060c8e62619b3369515f14ac00b72ff"
INCUMBENT_DEPLOYMENT = "2d608399-7172-4007-874e-17c8ed19bfa5"
LOCKED_SHA_ENV = "NFL_ML_BLIND_LOCKED_SHA"
PLATT_SCHEME = "inner_season_oof"
SIGMA_ROLE = "scoring_convention_not_production_distribution"


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


def final_seasons() -> list[int]:
    return list(range(FINAL_SEASON_FIRST, FINAL_SEASON_LAST + 1))


def test_seasons() -> list[int]:
    """Final-evaluation seasons. Selection seasons are not included."""
    return final_seasons()


def ats_cluster_lower_rate() -> float:
    return ATS_CLUSTER_LOWER_PP / 100.0


def spec_document() -> dict:
    """JSON-comparable copy of the lock constants. No commit SHA."""
    return {
        "seed": SEED,
        "sigma": SIGMA,
        "sigma_role": SIGMA_ROLE,
        "result_lag_days": RESULT_LAG_DAYS,
        "train_first_season": TRAIN_FIRST_SEASON,
        "history_season": HISTORY_SEASON,
        "selection_seasons": list(SELECTION_SEASONS),
        "final_seasons": final_seasons(),
        "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
        "bootstrap_percentiles": list(BOOTSTRAP_PERCENTILES),
        "bootstrap_cluster": "season_week",
        "close_ni_upper": CLOSE_NI_UPPER,
        "ats_cluster_lower_pp": ATS_CLUSTER_LOWER_PP,
        "z_min": Z_MIN,
        "power_min": POWER_MIN,
        "mde_crps": MDE_CRPS,
        "z_crit_95": Z_CRIT_95,
        "incumbent": INCUMBENT_MODE,
        "frozen_prod_replay": FROZEN_PROD_REPLAY,
        "incumbent_model_version": INCUMBENT_MODEL_VERSION,
        "incumbent_commit": INCUMBENT_COMMIT,
        "incumbent_deployment": INCUMBENT_DEPLOYMENT,
        "platt": PLATT_SCHEME,
        "platt_fit": {
            "epochs": PLATT_EPOCHS,
            "learning_rate": PLATT_LEARNING_RATE,
            "min_rows": PLATT_MIN_ROWS,
        },
        "elo": {
            "k": ELO_K,
            "home_field": ELO_HOME_FIELD,
            "initial": ELO_INITIAL,
            "season_carry": ELO_SEASON_CARRY,
        },
        "epa_window": EPA_WINDOW,
        "epa_half_life_games": EPA_HALF_LIFE_GAMES,
        "features": list(FEATURE_COLUMNS),
        "spread_model": dict(SPREAD_MODEL_PARAMS),
        "win_model": dict(WIN_MODEL_PARAMS),
        "ship_enables_ml": False,
        "ship_authorizes_merge": False,
    }
