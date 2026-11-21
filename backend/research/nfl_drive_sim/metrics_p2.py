"""2018-only scorecards. This module does not load another season."""

from __future__ import annotations

import numpy as np
import pandas as pd


def _mae_bias(pred: np.ndarray, actual: np.ndarray) -> dict[str, float]:
    err = np.asarray(pred, dtype=float) - np.asarray(actual, dtype=float)
    err = err[np.isfinite(err)]
    if err.size == 0:
        return {"mae": float("nan"), "bias": float("nan"), "n": 0}
    return {"mae": float(np.mean(np.abs(err))), "bias": float(np.mean(err)), "n": int(err.size)}


def _pair(table: pd.DataFrame, pred_home: str, pred_away: str, act_home: str, act_away: str) -> dict[str, float]:
    pred = np.concatenate([table[pred_home].to_numpy(), table[pred_away].to_numpy()])
    actual = np.concatenate([table[act_home].to_numpy(), table[act_away].to_numpy()])
    return _mae_bias(pred, actual)


def pace_report(table: pd.DataFrame) -> dict:
    model_plays = _pair(table, "home_plays_mean", "away_plays_mean", "actual_home_plays", "actual_away_plays")
    base_plays = _pair(table, "base_home_plays", "base_away_plays", "actual_home_plays", "actual_away_plays")
    model_drives = _pair(table, "home_drives_mean", "away_drives_mean", "actual_home_drives", "actual_away_drives")
    base_drives = _pair(table, "base_home_drives", "base_away_drives", "actual_home_drives", "actual_away_drives")
    model_sec = _pair(table, "sim_home_sec", "sim_away_sec", "actual_home_sec", "actual_away_sec")
    base_sec = _pair(table, "base_home_sec", "base_away_sec", "actual_home_sec", "actual_away_sec")
    model_ppd = _pair(table, "sim_home_ppd", "sim_away_ppd", "actual_home_ppd", "actual_away_ppd")
    base_ppd = _pair(table, "base_home_ppd", "base_away_ppd", "actual_home_ppd", "actual_away_ppd")
    game_pred = table["home_plays_mean"] + table["away_plays_mean"]
    game_base = table["base_home_plays"] + table["base_away_plays"]
    inside_80 = (table["actual_plays"] >= table["plays_p10"]) & (table["actual_plays"] <= table["plays_p90"])
    inside_90 = (table["actual_plays"] >= table["plays_p05"]) & (table["actual_plays"] <= table["plays_p95"])
    return {
        "plays_per_team_model": model_plays,
        "plays_per_team_baseline": base_plays,
        "plays_per_game_model": _mae_bias(game_pred, table["actual_plays"]),
        "plays_per_game_baseline": _mae_bias(game_base, table["actual_plays"]),
        "drives_per_team_model": model_drives,
        "drives_per_team_baseline": base_drives,
        "seconds_per_play_model": model_sec,
        "seconds_per_play_baseline": base_sec,
        "plays_per_drive_model": model_ppd,
        "plays_per_drive_baseline": base_ppd,
        "game_plays_interval_80": float(inside_80.mean()),
        "game_plays_interval_90": float(inside_90.mean()),
    }


def sim_report(table: pd.DataFrame) -> dict:
    actual_drives = table["actual_home_drives"] + table["actual_away_drives"]
    sim_drives = table["home_drives_mean"] + table["away_drives_mean"]
    return {
        "actual_total_mean": float(table["actual_total"].mean()),
        "sim_total_mean": float(table["total_mean"].mean()),
        "actual_total_sd": float(table["actual_total"].std()),
        "sim_total_sd_mean": float(table["total_sd"].mean()),
        "actual_margin_mean": float(table["actual_margin"].mean()),
        "sim_margin_mean": float(table["margin_mean"].mean()),
        "actual_margin_sd": float(table["actual_margin"].std()),
        "sim_margin_sd_mean": float(table["margin_sd"].mean()),
        "actual_home_win_rate": float(table["actual_home_win"].mean()),
        "sim_home_win_rate": float(table["p_home_win"].mean()),
        "actual_ot_rate": float(table["actual_ot"].mean()),
        "sim_ot_rate": float(table["ot_rate"].mean()),
        "actual_drives_mean": float(actual_drives.mean()),
        "sim_drives_mean": float(sim_drives.mean()),
        "actual_drives_sd": float(actual_drives.std()),
        "sim_drives_sd_mean": float(table["drives_sd"].mean()),
        "actual_share_margin_3": float(table["actual_margin"].abs().eq(3).mean()),
        "sim_share_margin_3": float(table["sim_p_margin_3"].mean()),
        "actual_share_margin_7": float(table["actual_margin"].abs().eq(7).mean()),
        "sim_share_margin_7": float(table["sim_p_margin_7"].mean()),
    }
