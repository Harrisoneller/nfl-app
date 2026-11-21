"""Week-level pace, yards-per-play, and drive-outcome fits.

A week's coefficients use only games that kicked off before that week's first
kickoff. Feature values on a later game in the week still include earlier
games that week, because those kickoffs are already in the past.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import sim_config as cfg
from .models import fit_ridge, fit_softmax, predict_ridge, softmax

PACE_STATE = {
    "neutral": ("sec_neutral", "ppd_neutral"),
    "leading": ("sec_leading", "ppd_leading"),
    "trailing": ("sec_trailing", "ppd_trailing"),
    "two_minute": ("sec_two_minute", "ppd_two_minute"),
}


def _matrix(frame: pd.DataFrame, names: tuple[str, ...], columns: dict[str, str]) -> np.ndarray:
    data = [np.ones(len(frame))]
    for name in names:
        if name == "intercept":
            continue
        source = columns[name]
        data.append(pd.to_numeric(frame[source], errors="coerce").fillna(0.0).to_numpy(dtype=float))
    return np.column_stack(data)


def _scale(train: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mu = train.mean(axis=0)
    sd = train.std(axis=0)
    sd[sd < 1e-6] = 1.0
    mu[0] = 0.0
    sd[0] = 1.0
    return mu, sd


def _apply(x: np.ndarray, mu: np.ndarray, sd: np.ndarray) -> np.ndarray:
    return (x - mu) / sd


def _pace_columns(state: str, target: str) -> dict[str, str]:
    sec_name, ppd_name = PACE_STATE[state]
    rate = sec_name if target == "sec" else ppd_name
    return {
        "off_rate": f"off_{rate}",
        # The opponent's defense, not this team's own defense.
        "def_rate": f"opp_def_{rate}",
        "no_huddle": "off_no_huddle",
        "incomplete": "off_incomplete",
        "pass_rate": "off_pass_rate",
        "dome": "dome",
        "wind": "wind",
        "home": "is_home",
    }


def fit_pace(train: pd.DataFrame) -> dict:
    models = {}
    for state in cfg.SITUATIONS:
        for target, actual in (("sec", PACE_STATE[state][0]), ("plays", PACE_STATE[state][1])):
            columns = _pace_columns(state, target)
            usable = train.dropna(subset=[actual])
            if target == "sec":
                count_col = actual.replace("sec_", "plays_")
                if count_col in usable.columns:
                    usable = usable[pd.to_numeric(usable[count_col], errors="coerce").fillna(0) >= cfg.MIN_STATE_PLAYS]
            if len(usable) < 30:
                models[(state, target)] = None
                continue
            x = _matrix(usable, cfg.PACE_FEATURES, columns)
            y = pd.to_numeric(usable[actual], errors="coerce").fillna(0).to_numpy(dtype=float)
            mu, sd = _scale(x)
            coef = fit_ridge(_apply(x, mu, sd), y, cfg.PACE_RIDGE_ALPHA)
            models[(state, target)] = {"coef": coef, "mu": mu, "sd": sd, "columns": columns}
    return models


def predict_pace(row: pd.Series, models: dict) -> dict[str, dict[str, float]]:
    out = {}
    for state in cfg.SITUATIONS:
        out[state] = {}
        for target in ("sec", "plays"):
            model = models.get((state, target))
            fallback = 25.0 if target == "sec" else 6.0
            if model is None:
                out[state][target] = fallback
                continue
            frame = row.to_frame().T
            x = _matrix(frame, cfg.PACE_FEATURES, model["columns"])
            pred = float(predict_ridge(_apply(x, model["mu"], model["sd"]), model["coef"])[0])
            if not np.isfinite(pred):
                pred = fallback
            if target == "sec":
                pred = float(np.clip(pred, 8.0, 40.0))
            else:
                pred = float(np.clip(pred, 2.0, 12.0))
            out[state][target] = pred
    return out


def _ypp_columns(kind: str) -> dict[str, str]:
    explosive = "expl_pass" if kind == "pass" else "expl_run"
    return {
        "off_epa": "off_epa",
        "def_epa": "def_epa",
        "off_success": "off_success",
        "def_success": "def_success",
        "off_explosive": f"off_{explosive}",
        "def_explosive": f"def_{explosive}",
        "early_pass": "off_early_pass",
        "off_sack": "off_sack",
        "def_sack": "def_sack",
        "off_pressure": "off_pressure",
        "def_pressure": "def_pressure",
        "qb_epa": "qb_epa",
        "qb_cpoe": "qb_cpoe",
        "home": "is_home",
        "dome": "dome",
    }


def fit_ypp(train: pd.DataFrame, enabled: bool) -> dict | None:
    if not enabled or len(train) < 30:
        return None
    models = {}
    for name, actual in (("pass", "ypp_pass"), ("run", "ypp_run")):
        columns = _ypp_columns(name)
        x = _matrix(train, cfg.YPP_FEATURES, columns)
        mu, sd = _scale(x)
        y = pd.to_numeric(train[actual], errors="coerce").fillna(0).to_numpy(dtype=float)
        models[name] = {
            "coef": fit_ridge(_apply(x, mu, sd), y, cfg.YPP_RIDGE_ALPHA),
            "mu": mu,
            "sd": sd,
            "columns": columns,
        }
    return models


def predict_ypp(row: pd.Series, model: dict | None, enabled: bool) -> tuple[float, float]:
    if not enabled or model is None:
        return 0.0, 0.0
    frame = row.to_frame().T
    out = []
    for name in ("pass", "run"):
        part = model[name]
        x = _apply(_matrix(frame, cfg.YPP_FEATURES, part["columns"]), part["mu"], part["sd"])
        out.append(float(predict_ridge(x, part["coef"])[0]))
    return out[0], out[1]


def _drive_feature_frame(drives: pd.DataFrame, team_rows: pd.DataFrame, ypp_on: bool) -> pd.DataFrame:
    sides = team_rows.copy()
    keep = [
        "game_id", "team", "off_td_rate", "off_fg_rate", "off_punt_rate", "off_to_rate",
        "def_td_rate", "def_to_rate", "qb_epa", "dome", "is_home", "ypp_pass_hat", "ypp_run_hat",
    ]
    for column in keep:
        if column not in sides.columns:
            sides[column] = 0.0
    merged = drives.merge(
        sides[keep], left_on=["game_id", "posteam"], right_on=["game_id", "team"], how="inner",
    )
    merged["yardline_100"] = pd.to_numeric(merged["start_yardline_100"], errors="coerce").fillna(75)
    merged["score_diff"] = pd.to_numeric(merged["start_score_diff"], errors="coerce").fillna(0)
    merged["half_seconds"] = pd.to_numeric(merged["start_half_seconds_remaining"], errors="coerce").fillna(1800)
    off_to = pd.to_numeric(merged["start_posteam_timeouts"], errors="coerce").fillna(3)
    def_to = pd.to_numeric(merged["start_defteam_timeouts"], errors="coerce").fillna(3)
    merged["timeouts"] = off_to + def_to
    merged["fg_range"] = (merged["yardline_100"] <= cfg.FG_RANGE_YARDLINE).astype(float)
    merged["red_zone"] = (merged["yardline_100"] <= cfg.RED_ZONE_YARDLINE).astype(float)
    merged["two_minute"] = (merged["half_seconds"] <= cfg.TWO_MINUTE_SECONDS).astype(float)
    merged["trailing"] = (merged["score_diff"] < -cfg.NEUTRAL_SCORE).astype(float)
    merged["home"] = merged["is_home"]
    merged["off_td"] = merged["off_td_rate"]
    merged["off_fg"] = merged["off_fg_rate"]
    merged["off_punt"] = merged["off_punt_rate"]
    merged["off_to"] = merged["off_to_rate"]
    merged["def_td"] = merged["def_td_rate"]
    merged["def_to"] = merged["def_to_rate"]
    merged["ypp_pass"] = merged["ypp_pass_hat"] if ypp_on else 0.0
    merged["ypp_run"] = merged["ypp_run_hat"] if ypp_on else 0.0
    return merged


def fit_drive_model(drives: pd.DataFrame, team_rows: pd.DataFrame, ypp_on: bool) -> dict | None:
    frame = _drive_feature_frame(drives, team_rows, ypp_on)
    frame = frame[frame["outcome"].isin(cfg.DRIVE_OUTCOMES)]
    if len(frame) < 50:
        return None
    names = [name for name in cfg.DRIVE_FEATURES if name != "intercept"]
    x = np.column_stack([np.ones(len(frame))] + [
        pd.to_numeric(frame[name], errors="coerce").fillna(0).to_numpy(dtype=float) for name in names
    ])
    y = frame["outcome"].map({label: i for i, label in enumerate(cfg.DRIVE_OUTCOMES)}).to_numpy(dtype=int)
    present = set(np.unique(y))
    if len(present) < 2:
        return None
    mu, sd = _scale(x)
    weights = fit_softmax(_apply(x, mu, sd), y, len(cfg.DRIVE_OUTCOMES), cfg.DRIVE_LOGIT_L2, cfg.DRIVE_LOGIT_ITERS)
    return {"weights": weights, "mu": mu, "sd": sd, "names": names}


def drive_feature_vector(state: dict, model: dict) -> np.ndarray:
    values = [1.0] + [float(state.get(name, 0.0)) for name in model["names"]]
    return np.asarray(values, dtype=float)


def outcome_probabilities(features: np.ndarray, model: dict) -> np.ndarray:
    scaled = _apply(features, model["mu"], model["sd"])
    return softmax(scaled @ model["weights"])
