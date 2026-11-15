"""Walk-forward stage-2, paired CRPS, and the exploratory MDE.

Both the base and the candidate are refit on seasons strictly before the
test season. Shipped stage-2 weights are not an argument and are not read.

``sd(d)`` for the MDE is the sample standard deviation of the base replay's
per-game CRPS, divisor ``n - 1``. It is computed from that replay before the
candidate is fit. It is not the standard deviation of the candidate's paired
CRPS differences.

The screen grades kickoffs before 2027-01-01 Eastern. January 2027 is a
harm check. The confirmatory sample is the 2027 regular season.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from app.services.prediction_dist import norm_ppf

from .calendar import before_screen_cutoff, eastern_kickoff, is_january_2027_kickoff
from .crps import per_game_crps
from .design import DesignError, assert_single_addition, complete_rows, fit_ols, predict
from .holm import (
    bootstrap_quantile,
    cluster_robust_t,
    holm_stepdown,
    one_sided_p_negative,
    sign_matches,
    wild_cluster_replicates,
)
from .specs import (
    ATS_LB_LIMIT,
    ATS_LB_QUANTILE,
    CONFIRM_CLUSTERS_EXPECTED,
    CONFIRM_N_EXPECTED,
    CONFIRM_SEASON,
    CONFIRM_WEEK_FIRST,
    CONFIRM_WEEK_LAST,
    EXPLORATORY_TEST_FIRST,
    EXPLORATORY_TEST_LAST,
    FEATURES,
    HARM_CLUSTERS_EXPECTED,
    HARM_N_EXPECTED,
    HARM_SEASON,
    HARM_UB_LIMIT,
    HOLM_ALPHA,
    HOLM_ALPHA_STAR,
    POWER,
    REGIME_LAST,
    SCREEN_UB_QUANTILE,
    STRUCTURED_SHIFT_POINTS,
    TRAIN_FIRST,
)

_GAME_TYPES = frozenset({"REG", "WC", "DIV", "CON", "SB"})


def sample_sd(values) -> float:
    """Sample standard deviation with divisor ``n - 1``."""
    arr = np.asarray(values, dtype=float)
    if len(arr) < 2 or not np.isfinite(arr).all():
        raise DesignError("sd(d) needs at least two finite games")
    return float(np.std(arr, ddof=1))


def minimum_detectable_effect(
    sd: float,
    n: int,
    *,
    alpha: float = HOLM_ALPHA_STAR,
    beta: float = 1.0 - POWER,
) -> float:
    """``MDE = (z_alpha + z_(1-beta)) * sd / sqrt(n)``.

    The default power is 80 percent. ``beta = 0.5`` is not the default.
    ``alpha`` defaults to ``0.025 / 3``, the tightest Holm threshold.
    """
    if n < 2:
        raise DesignError("MDE needs at least two games")
    if not np.isfinite(sd) or sd < 0:
        raise DesignError("sd(d) must be finite and non-negative")
    z_alpha = float(norm_ppf(1.0 - alpha))
    z_power = float(norm_ppf(1.0 - beta))
    return (z_alpha + z_power) * float(sd) / math.sqrt(n)


def require_schedule_fields(games: pd.DataFrame) -> None:
    """Every stage needs season, week, kickoff, and game_type."""
    required = ("season", "week", "kickoff", "game_type")
    missing = [col for col in required if col not in games.columns]
    if missing:
        raise DesignError(f"schedule fields required: {missing}")
    if (games["season"].astype(int) <= REGIME_LAST).any():
        raise DesignError("seasons through 2012 are a different regime and are not in this study")
    if games["kickoff"].map(lambda value: eastern_kickoff(value) is None).any():
        raise DesignError("every row needs an America/New_York kickoff")
    kinds = set(games["game_type"].astype(str))
    if not kinds <= _GAME_TYPES:
        raise DesignError(f"game_type must be REG or a playoff round, got {sorted(kinds - _GAME_TYPES)}")


def _feature_spec(feature: str) -> dict[str, object]:
    if feature not in FEATURES:
        raise DesignError(f"unknown feature {feature}")
    return FEATURES[feature]


def _split(
    games: pd.DataFrame,
    columns: tuple[str, ...],
    target: str,
    test_season: int,
    *,
    weeks: tuple[int, ...] | None,
    january_2027: bool = False,
    regular_only: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    data = complete_rows(games, columns, target)
    data = data[data["season"].astype(int) >= TRAIN_FIRST]
    if "kickoff" in data.columns and not january_2027:
        data = data[data["kickoff"].map(before_screen_cutoff)]
    train = data[data["season"].astype(int) < test_season]
    test = data[data["season"].astype(int) == test_season]
    if weeks is not None:
        test = test[test["week"].astype(int).isin(weeks)]
    if january_2027:
        if "kickoff" not in test.columns:
            test = test.iloc[0:0]
        else:
            test = test[test["kickoff"].map(is_january_2027_kickoff)]
    elif "game_type" in test.columns:
        allowed = {"REG"} if regular_only else _GAME_TYPES
        test = test[test["game_type"].astype(str).isin(allowed)]
    return train, test


def _origin_split(
    games: pd.DataFrame,
    feature: str,
    test_season: int,
    *,
    weeks: tuple[int, ...] | None,
    january_2027: bool = False,
    regular_only: bool = True,
) -> dict[str, object] | None:
    """Rows for one origin. The feature must be finite so both fits share a sample."""
    spec = _feature_spec(feature)
    base = tuple(spec["base"])  # type: ignore[arg-type]
    target = str(spec["target"])
    intercept = bool(spec["intercept"])
    candidate_columns = assert_single_addition(base, feature)
    train, test = _split(
        games, candidate_columns, target, test_season,
        weeks=weeks, january_2027=january_2027, regular_only=regular_only,
    )
    width = len(candidate_columns) + (1 if intercept else 0)
    if len(train) < width or len(test) == 0:
        return None
    return {
        "spec": spec,
        "base": base,
        "target": target,
        "intercept": intercept,
        "sigma": float(spec["sigma"]),
        "candidate_columns": candidate_columns,
        "train": train,
        "test": test,
    }


def base_replay_origin(
    games: pd.DataFrame,
    feature: str,
    test_season: int,
    *,
    weeks: tuple[int, ...] | None = None,
    january_2027: bool = False,
    regular_only: bool = True,
) -> dict[str, object] | None:
    """Walk-forward base fit only. Does not fit the candidate and does not read its values
    except to drop rows where the feature is missing, so the replay matches the paired sample.
    """
    origin = _origin_split(
        games, feature, test_season,
        weeks=weeks, january_2027=january_2027, regular_only=regular_only,
    )
    if origin is None:
        return None
    base = tuple(origin["base"])  # type: ignore[arg-type]
    target = str(origin["target"])
    intercept = bool(origin["intercept"])
    train = origin["train"]
    test = origin["test"]
    assert isinstance(train, pd.DataFrame) and isinstance(test, pd.DataFrame)
    base_coef = fit_ols(train, base, target, intercept=intercept)
    base_hat = predict(test, base_coef, base, intercept=intercept)
    actual = test[target].to_numpy(dtype=float)
    base_crps = per_game_crps(base_hat, float(origin["sigma"]), actual)
    graded = test[["season", "week"]].copy()
    graded["actual"] = actual
    graded["pred_base"] = base_hat
    graded["crps_base"] = base_crps
    return {
        "feature": feature,
        "test_season": test_season,
        "base_coefficients": base_coef,
        "graded": graded.reset_index(drop=True),
        "origin": origin,
    }


def grade_from_replay(replay: dict[str, object]) -> dict[str, object]:
    """Fit the candidate after the base replay exists. Base predictions are not refit."""
    origin = replay["origin"]
    assert isinstance(origin, dict)
    feature = str(replay["feature"])
    columns = tuple(origin["candidate_columns"])  # type: ignore[arg-type]
    target = str(origin["target"])
    intercept = bool(origin["intercept"])
    train = origin["train"]
    test = origin["test"]
    assert isinstance(train, pd.DataFrame) and isinstance(test, pd.DataFrame)
    cand_coef = fit_ols(train, columns, target, intercept=intercept)
    cand_hat = predict(test, cand_coef, columns, intercept=intercept)
    graded = replay["graded"]
    assert isinstance(graded, pd.DataFrame)
    actual = graded["actual"].to_numpy(dtype=float)
    cand_crps = per_game_crps(cand_hat, float(origin["sigma"]), actual)
    out = graded.copy()
    out["pred_candidate"] = cand_hat
    out["crps_candidate"] = cand_crps
    out["delta"] = cand_crps - out["crps_base"].to_numpy(dtype=float)
    out["mae_base"] = np.abs(out["pred_base"].to_numpy(dtype=float) - actual)
    out["mae_candidate"] = np.abs(cand_hat - actual)
    base_coef = replay["base_coefficients"]
    assert isinstance(base_coef, dict)
    return {
        "feature": feature,
        "test_season": int(replay["test_season"]),
        "coefficient": float(cand_coef[feature]),
        "base_coefficients": base_coef,
        "candidate_coefficients": cand_coef,
        "graded": out.reset_index(drop=True),
    }


def _seasons_present(games: pd.DataFrame, first: int, last: int) -> list[int]:
    seasons = sorted(int(s) for s in games["season"].astype(int).unique())
    return [season for season in seasons if first <= season <= last]


def structured_shift_sd(predicted, actual, sigma: float, shift: float = STRUCTURED_SHIFT_POINTS) -> float:
    """Sample sd of CRPS(prediction + shift) minus CRPS(prediction).

    One preregistered shift of +1 point. Not a noise placebo. Measured before
    any candidate coefficient is fit. Descriptive only.
    """
    base = per_game_crps(predicted, sigma, actual)
    moved = per_game_crps(np.asarray(predicted, dtype=float) + shift, sigma, actual)
    return sample_sd(moved - base)


def screen_retain(mean_delta: float, upper_90: float, coefficients: list[float], feature: str) -> bool:
    """Both continuation rules. A miss drops the feature. There is no retune."""
    expected = int(FEATURES[feature]["expected_sign"])
    signs_ok = bool(coefficients) and all(sign_matches(coef, expected) for coef in coefficients)
    return bool(mean_delta < 0.0 and signs_ok and upper_90 < 0.0)


def exploratory_screen(games: pd.DataFrame, feature: str) -> dict[str, object]:
    """Walk-forward on kickoffs before 2027-01-01 Eastern. Exploratory only.

    January 2027 rows are excluded. A feature is retained only when the mean
    paired CRPS difference is negative, every season coefficient matches the
    preregistered sign, and the wild-cluster 90% upper bound is negative.
    """
    require_schedule_fields(games)
    spec = FEATURES[feature]
    replays = []
    skipped: list[int] = []
    for season in _seasons_present(games, EXPLORATORY_TEST_FIRST, EXPLORATORY_TEST_LAST):
        replay = base_replay_origin(games, feature, season, regular_only=False)
        if replay is None:
            skipped.append(season)
            continue
        replays.append(replay)
    if not replays:
        raise DesignError("exploratory screen produced no graded games")
    replay_frame = pd.concat([item["graded"] for item in replays], ignore_index=True)
    assert isinstance(replay_frame, pd.DataFrame)
    shift_sd = structured_shift_sd(
        replay_frame["pred_base"], replay_frame["actual"], float(spec["sigma"]),
    )
    n = len(replay_frame)
    pieces = []
    coefficients: list[dict[str, float | int]] = []
    for replay in replays:
        graded_origin = grade_from_replay(replay)
        pieces.append(graded_origin["graded"])
        coefficients.append({
            "season": int(graded_origin["test_season"]),
            "coefficient": float(graded_origin["coefficient"]),
        })
    graded = pd.concat(pieces, ignore_index=True)
    mean_delta = float(graded["delta"].mean())
    replicates = wild_cluster_replicates(graded["delta"], graded["season"], graded["week"])
    upper_90 = bootstrap_quantile(replicates, SCREEN_UB_QUANTILE)
    coefs = [float(row["coefficient"]) for row in coefficients]
    retain = screen_retain(mean_delta, upper_90, coefs, feature)
    return {
        "role": "exploratory_screen",
        "exploratory": True,
        "confirmatory": False,
        "ship_gate": False,
        "feature": feature,
        "endpoint": spec["endpoint"],
        "n": n,
        "seasons_graded": [int(row["season"]) for row in coefficients],
        "seasons_skipped": skipped,
        "structured_shift_sd": shift_sd,
        "mde_80": minimum_detectable_effect(shift_sd, n),
        "mean_delta": mean_delta,
        "upper_90": upper_90,
        "mae_base": float(graded["mae_base"].mean()),
        "mae_candidate": float(graded["mae_candidate"].mean()),
        "coefficients": coefficients,
        "retain": retain,
        "dropped": not retain,
        "cluster_robust_t": cluster_robust_t(graded["delta"], graded["season"], graded["week"]),
        "graded": graded,
    }


def _fit_frozen(train: pd.DataFrame, test: pd.DataFrame, feature: str) -> dict[str, object]:
    """One fit on ``train``, scored on ``test``. The candidate is not refit on ``test``."""
    spec = FEATURES[feature]
    base = tuple(spec["base"])  # type: ignore[arg-type]
    target = str(spec["target"])
    intercept = bool(spec["intercept"])
    columns = assert_single_addition(base, feature)
    train_rows = complete_rows(train, columns, target)
    test_rows = complete_rows(test, columns, target)
    width = len(columns) + (1 if intercept else 0)
    if len(train_rows) < width or len(test_rows) == 0:
        raise DesignError(f"{feature} does not have a frozen train and test split")
    base_coef = fit_ols(train_rows, base, target, intercept=intercept)
    base_hat = predict(test_rows, base_coef, base, intercept=intercept)
    actual = test_rows[target].to_numpy(dtype=float)
    base_crps = per_game_crps(base_hat, float(spec["sigma"]), actual)
    cand_coef = fit_ols(train_rows, columns, target, intercept=intercept)
    cand_hat = predict(test_rows, cand_coef, columns, intercept=intercept)
    cand_crps = per_game_crps(cand_hat, float(spec["sigma"]), actual)
    graded = test_rows[["season", "week", "kickoff", "game_type"]].copy()
    graded["actual"] = actual
    graded["pred_base"] = base_hat
    graded["pred_candidate"] = cand_hat
    graded["delta"] = cand_crps - base_crps
    for col in ("spread_line", "total_line"):
        if col in test_rows.columns:
            graded[col] = test_rows[col].to_numpy()
    return {
        "coefficient": float(cand_coef[feature]),
        "graded": graded.reset_index(drop=True),
    }


def harm_check(games: pd.DataFrame, feature: str) -> dict[str, object]:
    """January 2027 kickoffs. A pass means no detectable harm, not a benefit.

    The candidate is fit only on kickoffs before 2027-01-01. The wild-cluster
    90% upper bound of mean paired CRPS must be <= +0.25. A miss stops the feature.
    """
    require_schedule_fields(games)
    train = games[games["kickoff"].map(before_screen_cutoff)].copy()
    test = games[
        (games["season"].astype(int) == HARM_SEASON) & games["kickoff"].map(is_january_2027_kickoff)
    ].copy()
    if len(test) == 0:
        raise DesignError("harm check has no January 2027 kickoffs")
    frozen = _fit_frozen(train, test, feature)
    graded = frozen["graded"]
    assert isinstance(graded, pd.DataFrame)
    replicates = wild_cluster_replicates(graded["delta"], graded["season"], graded["week"])
    upper_90 = bootstrap_quantile(replicates, SCREEN_UB_QUANTILE)
    no_harm = bool(upper_90 <= HARM_UB_LIMIT)
    return {
        "role": "harm_check",
        "benefit_claim": False,
        "feature": feature,
        "n": len(graded),
        "clusters": int(graded.groupby(["season", "week"]).ngroups),
        "expected_n": HARM_N_EXPECTED,
        "expected_clusters": HARM_CLUSTERS_EXPECTED,
        "upper_90": upper_90,
        "limit": HARM_UB_LIMIT,
        "no_harm": no_harm,
        "stopped": not no_harm,
        "coefficient": frozen["coefficient"],
        "graded": graded,
    }


def closing_line_edge(graded: pd.DataFrame, endpoint: str) -> np.ndarray:
    """Cover indicator minus one half. The closing line is not a regressor.

    Margin uses ``spread_line`` (positive means the home team is favored).
    The model bets home when its margin is above the line and away when it is
    below. Total uses ``total_line`` the same way. Pushes and exact ties with
    the line are dropped by the caller.
    """
    actual = graded["actual"].to_numpy(dtype=float)
    pred = graded["pred_candidate"].to_numpy(dtype=float)
    line_name = "spread_line" if endpoint == "margin" else "total_line"
    if line_name not in graded.columns:
        raise DesignError(f"ATS grading needs {line_name}")
    line = graded[line_name].to_numpy(dtype=float)
    cover = np.full(len(graded), np.nan)
    bet_home = pred > line
    bet_away = pred < line
    cover[bet_home & (actual > line)] = 1.0
    cover[bet_home & (actual < line)] = 0.0
    cover[bet_away & (actual < line)] = 1.0
    cover[bet_away & (actual > line)] = 0.0
    return cover - 0.5


def confirmatory_look(games: pd.DataFrame, features: list[str]) -> dict[str, object]:
    """One look at the 2027 regular season for features that reached this stage.

    Coefficients are fit on kickoffs before the first 2027 regular-season
    kickoff, which may include January 2027 and the Super Bowl. Holm uses
    alpha 0.025/k. A miss is inconclusive and does not ship.
    """
    if not features:
        raise DesignError("confirmatory stage has no retained features")
    require_schedule_fields(games)
    week = games["week"].astype(int)
    season = games["season"].astype(int)
    test_mask = (
        (season == CONFIRM_SEASON)
        & games["game_type"].astype(str).eq("REG")
        & week.between(CONFIRM_WEEK_FIRST, CONFIRM_WEEK_LAST)
    )
    test = games.loc[test_mask].copy()
    if len(test) == 0:
        raise DesignError("confirmatory stage has no 2027 regular-season games")
    first_kick = min(eastern_kickoff(value) for value in test["kickoff"])
    train = games.loc[~test_mask].copy()
    train = train[train["kickoff"].map(lambda value: eastern_kickoff(value) < first_kick)].copy()
    k = len(features)
    alpha = HOLM_ALPHA / k
    decisions = []
    p_values: dict[str, float] = {}
    details: dict[str, dict[str, object]] = {}
    for feature in features:
        frozen = _fit_frozen(train, test, feature)
        graded = frozen["graded"]
        assert isinstance(graded, pd.DataFrame)
        observed = float(graded["delta"].mean())
        null_draws = wild_cluster_replicates(
            graded["delta"], graded["season"], graded["week"], null_mean=0.0,
        )
        p_values[feature] = one_sided_p_negative(observed, null_draws)
        edge = closing_line_edge(graded, str(FEATURES[feature]["endpoint"]))
        keep = np.isfinite(edge)
        if int(keep.sum()) < 2:
            raise DesignError("ATS guard needs at least two non-push games")
        ats_draws = wild_cluster_replicates(
            edge[keep],
            graded.loc[keep, "season"],
            graded.loc[keep, "week"],
        )
        ats_lb = bootstrap_quantile(ats_draws, ATS_LB_QUANTILE)
        details[feature] = {
            "coefficient": frozen["coefficient"],
            "mean_delta": observed,
            "ats_lb": ats_lb,
            "n": len(graded),
        }
    holm_rows = holm_stepdown(p_values, alpha=HOLM_ALPHA)
    rejected = {row["feature"]: row["reject"] for row in holm_rows}
    for feature, payload in details.items():
        expected = int(FEATURES[feature]["expected_sign"])
        sign_ok = sign_matches(float(payload["coefficient"]), expected)
        ats_ok = bool(float(payload["ats_lb"]) >= ATS_LB_LIMIT)
        holm_ok = bool(rejected[feature])
        ship = bool(holm_ok and ats_ok and sign_ok)
        decisions.append({
            "feature": feature,
            "holm_reject": holm_ok,
            "sign_matches": sign_ok,
            "ats_lb": payload["ats_lb"],
            "ats_ok": ats_ok,
            "coefficient": payload["coefficient"],
            "verdict": "SHIP" if ship else "INCONCLUSIVE",
        })
    return {
        "role": "confirmatory",
        "confirmatory": True,
        "ship_gate": True,
        "k": k,
        "alpha_star": alpha,
        "expected_n": CONFIRM_N_EXPECTED,
        "expected_clusters": CONFIRM_CLUSTERS_EXPECTED,
        "mde_80_multiple": minimum_detectable_effect(1.0, CONFIRM_N_EXPECTED, alpha=alpha),
        "holm": holm_rows,
        "decisions": decisions,
    }


def run_stages(games: pd.DataFrame) -> dict[str, object]:
    """Screen, then harm-check survivors, then confirm whoever is left.

    A feature that fails the screen is not harm-checked. A feature that fails
    the harm check is not confirmed. Nothing is retuned.
    """
    screens: dict[str, dict[str, object]] = {}
    retained: list[str] = []
    for feature in FEATURES:
        screen = exploratory_screen(games, feature)
        screens[feature] = screen
        if screen["retain"]:
            retained.append(feature)
    harms: dict[str, dict[str, object]] = {}
    survivors: list[str] = []
    for feature in retained:
        harm = harm_check(games, feature)
        harms[feature] = harm
        if harm["no_harm"]:
            survivors.append(feature)
    confirmatory = confirmatory_look(games, survivors) if survivors else None
    return {
        "screens": screens,
        "retained": retained,
        "harms": harms,
        "survivors": survivors,
        "confirmatory": confirmatory,
    }
