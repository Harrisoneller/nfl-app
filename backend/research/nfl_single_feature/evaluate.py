"""Walk-forward stage-2, paired CRPS, and the exploratory MDE.

Both the base and the candidate are refit on seasons strictly before the
test season. Shipped stage-2 weights are not an argument and are not read.

``sd(d)`` for the MDE is the sample standard deviation of the base replay's
per-game CRPS, divisor ``n - 1``. It is computed from that replay before the
candidate is fit. It is not the standard deviation of the candidate's paired
CRPS differences.

Any grade whose test seasons fall in 2016–2026 is an exploratory screen.
The only confirmatory sample is 2027 regular-season weeks 1–4.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from app.services.prediction_dist import norm_ppf

from .crps import per_game_crps
from .design import DesignError, assert_single_addition, complete_rows, fit_ols, predict
from .holm import (
    assert_confirmatory_family,
    cluster_bootstrap_means,
    decide_feature,
    holm_stepdown,
    one_sided_p_negative,
    upper_bound,
)
from .specs import (
    CONFIRMATORY_SEASON,
    CONFIRMATORY_WEEKS,
    EXPLORATORY_TEST_FIRST,
    EXPLORATORY_TEST_LAST,
    FEATURES,
    HOLM_ALPHA_STAR,
    POWER_BETA,
    REGIME_LAST,
    TRAIN_FIRST,
)


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
    beta: float = POWER_BETA,
) -> float:
    """``MDE = (z_alpha* + z_(1-beta)) * sd / sqrt(n)``.

    ``alpha`` defaults to Holm's tightest one-sided threshold, ``0.025 / 3``.
    ``beta`` defaults to 0.5, so ``z_(1-beta)`` is 0.
    """
    if n < 2:
        raise DesignError("MDE needs at least two games")
    if not np.isfinite(sd) or sd < 0:
        raise DesignError("sd(d) must be finite and non-negative")
    z_alpha = float(norm_ppf(1.0 - alpha))
    z_power = float(norm_ppf(1.0 - beta))
    return (z_alpha + z_power) * float(sd) / math.sqrt(n)


def assert_study_frame(games: pd.DataFrame, *, confirmatory: bool) -> None:
    """Reject the pre-2013 regime, and keep 2027 weeks 5+ out of a confirmatory frame."""
    if "season" not in games.columns or "week" not in games.columns:
        raise DesignError("season and week are required")
    season = games["season"].astype(int)
    week = games["week"].astype(int)
    if (season <= REGIME_LAST).any():
        raise DesignError("seasons through 2012 are a different regime and are not in this study")
    if confirmatory:
        outside = (season > CONFIRMATORY_SEASON) | (
            (season == CONFIRMATORY_SEASON) & ~week.isin(CONFIRMATORY_WEEKS)
        )
        if outside.any():
            raise DesignError("confirmatory frame includes games outside 2027 regular-season weeks 1-4")
        inside = (season == CONFIRMATORY_SEASON) & week.isin(CONFIRMATORY_WEEKS)
        if not inside.any():
            raise DesignError("confirmatory frame has no 2027 weeks 1-4")
        if "game_type" in games.columns and games.loc[inside, "game_type"].astype(str).ne("REG").any():
            raise DesignError("confirmatory games must be game_type REG")
    elif (season >= CONFIRMATORY_SEASON).any():
        raise DesignError("2027 is the confirmatory sample and cannot be graded as an exploratory screen")


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
) -> tuple[pd.DataFrame, pd.DataFrame]:
    data = complete_rows(games, columns, target)
    data = data[data["season"].astype(int) >= TRAIN_FIRST]
    train = data[data["season"].astype(int) < test_season]
    test = data[data["season"].astype(int) == test_season]
    if weeks is not None:
        test = test[test["week"].astype(int).isin(weeks)]
    if "game_type" in test.columns:
        test = test[test["game_type"].astype(str).eq("REG")]
    return train, test


def _origin_split(
    games: pd.DataFrame,
    feature: str,
    test_season: int,
    *,
    weeks: tuple[int, ...] | None,
) -> dict[str, object] | None:
    """Rows for one origin. The feature must be finite so both fits share a sample."""
    spec = _feature_spec(feature)
    base = tuple(spec["base"])  # type: ignore[arg-type]
    target = str(spec["target"])
    intercept = bool(spec["intercept"])
    candidate_columns = assert_single_addition(base, feature)
    train, test = _split(games, candidate_columns, target, test_season, weeks=weeks)
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
) -> dict[str, object] | None:
    """Walk-forward base fit only. Does not fit the candidate and does not read its values
    except to drop rows where the feature is missing, so the replay matches the paired sample.
    """
    origin = _origin_split(games, feature, test_season, weeks=weeks)
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


def exploratory_screen(games: pd.DataFrame, feature: str) -> dict[str, object]:
    """Grade 2016–2026 seasons present in ``games``. This is not a ship gate.

    The MDE uses the pooled base-replay CRPS. That replay is fit before any
    candidate coefficient on this feature. The screen bar is
    ``mean(delta) <= -MDE``. Clearing it does not remove or add a feature
    and does not authorize the 2027 look to drop a feature.
    """
    assert_study_frame(games, confirmatory=False)
    replays = []
    skipped: list[int] = []
    for season in _seasons_present(games, EXPLORATORY_TEST_FIRST, EXPLORATORY_TEST_LAST):
        replay = base_replay_origin(games, feature, season)
        if replay is None:
            skipped.append(season)
            continue
        replays.append(replay)
    if not replays:
        raise DesignError("exploratory screen produced no graded games")
    replay_frame = pd.concat([replay["graded"] for replay in replays], ignore_index=True)
    assert isinstance(replay_frame, pd.DataFrame)
    replay_sd = sample_sd(replay_frame["crps_base"])
    n = len(replay_frame)
    mde = minimum_detectable_effect(replay_sd, n)
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
    return {
        "role": "exploratory_screen",
        "confirmatory": False,
        "ship_gate": False,
        "feature": feature,
        "endpoint": FEATURES[feature]["endpoint"],
        "n": n,
        "seasons_graded": [row["season"] for row in coefficients],
        "seasons_skipped": skipped,
        "replay_sd": replay_sd,
        "mde": mde,
        "mean_delta": mean_delta,
        "mae_base": float(graded["mae_base"].mean()),
        "mae_candidate": float(graded["mae_candidate"].mean()),
        "mde_bar_clear": bool(mean_delta <= -mde),
        "coefficients": coefficients,
        "graded": graded,
    }


def confirmatory_look(games: pd.DataFrame) -> dict[str, object]:
    """One look at 2027 weeks 1–4 for all three features, then Holm.

    The ship MDE is computed from each feature's base replay on its 2027
    rows, before any candidate fit. It is reported. The pass rule is Holm,
    the 97.5th-percentile upper bound, and the coefficient sign.
    """
    assert_study_frame(games, confirmatory=True)
    assert_confirmatory_family(list(FEATURES))
    replays: dict[str, dict[str, object]] = {}
    ship_mde: dict[str, dict[str, float | int | str]] = {}
    for feature in FEATURES:
        replay = base_replay_origin(
            games, feature, CONFIRMATORY_SEASON, weeks=CONFIRMATORY_WEEKS,
        )
        if replay is None:
            raise DesignError(f"confirmatory base replay could not fit {feature}")
        graded = replay["graded"]
        assert isinstance(graded, pd.DataFrame)
        if graded["week"].astype(int).nunique() < 2:
            raise DesignError("confirmatory bootstrap needs at least two 2027 weeks")
        replay_sd = sample_sd(graded["crps_base"])
        n = len(graded)
        replays[feature] = replay
        ship_mde[feature] = {
            "endpoint": str(FEATURES[feature]["endpoint"]),
            "replay_sd": replay_sd,
            "n": n,
            "ship_mde": minimum_detectable_effect(replay_sd, n),
        }
    p_values: dict[str, float] = {}
    graded_by_feature: dict[str, dict[str, object]] = {}
    for feature, replay in replays.items():
        graded_origin = grade_from_replay(replay)
        graded = graded_origin["graded"]
        assert isinstance(graded, pd.DataFrame)
        boot = cluster_bootstrap_means(graded["delta"], graded["season"], graded["week"])
        p_values[feature] = one_sided_p_negative(boot)
        graded_by_feature[feature] = {
            "coefficient": float(graded_origin["coefficient"]),
            "p": p_values[feature],
            "upper_bound": upper_bound(boot),
        }
    holm_rows = holm_stepdown(p_values)
    reject = {row["feature"]: row["reject"] for row in holm_rows}
    decisions = [
        decide_feature(
            feature=feature,
            p_value=float(payload["p"]),
            holm_reject=bool(reject[feature]),
            bound=float(payload["upper_bound"]),
            coef=float(payload["coefficient"]),
        )
        for feature, payload in graded_by_feature.items()
    ]
    return {
        "role": "confirmatory",
        "confirmatory": True,
        "ship_gate": True,
        "season": CONFIRMATORY_SEASON,
        "weeks": CONFIRMATORY_WEEKS,
        "holm": holm_rows,
        "decisions": decisions,
        "ship_mde": ship_mde,
        "n_passed": int(sum(bool(row["pass"]) for row in decisions)),
    }
