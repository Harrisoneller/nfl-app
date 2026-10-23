"""The QA harness replays the backtested v6-blind predictions."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "backtest" / "harness_model" / "model.py"
PREDS = ROOT / "backtest" / "harness_model" / "priors" / "predictions.csv"
FEATURES = ROOT / "backtest" / "harness_model" / "priors" / "week_features.csv"


def _load_harness():
    spec = importlib.util.spec_from_file_location("v6_harness_model", HARNESS)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _target_from(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.rename(columns={"gameday": "date"}).copy()
    out["neutral_site"] = 0
    out["location"] = "Home"
    out["spread_line"] = -3.5
    out["total_line"] = 44.5
    out["home_moneyline"] = -150
    return out


def test_priors_have_no_market_column():
    header = FEATURES.read_text().splitlines()[0].lower()
    for banned in ("spread", "moneyline", "kalshi", "adp", "total_line", "mkt"):
        assert banned not in header


def test_harness_reproduces_backtest_predictions():
    harness = _load_harness()
    preds = pd.read_csv(PREDS)
    sample = pd.concat([
        preds[preds["season"].between(2016, 2023)].iloc[::40],
        preds[preds["season"].between(2024, 2026)],
    ], ignore_index=True)
    target = _target_from(sample)
    hist = target[["game_id", "home_team", "away_team", "date", "season"]].copy()
    out = harness.predict(hist, target)
    assert list(out.columns) == [
        "game_id", "pred_margin", "pred_total", "pred_home_score", "pred_away_score",
    ]
    assert len(out) == len(target)
    assert out.index.equals(target.index)
    assert out["game_id"].tolist() == target["game_id"].tolist()
    assert out["pred_margin"].to_numpy() == pytest.approx(sample["margin"].to_numpy(), abs=1e-6)
    assert out["pred_total"].to_numpy() == pytest.approx(sample["total"].to_numpy(), abs=1e-6)
    home = (out["pred_total"] + out["pred_margin"]) / 2
    away = (out["pred_total"] - out["pred_margin"]) / 2
    assert out["pred_home_score"].to_numpy() == pytest.approx(home.to_numpy(), abs=1e-9)
    assert out["pred_away_score"].to_numpy() == pytest.approx(away.to_numpy(), abs=1e-9)


def _relabel_half(frame: pd.DataFrame, column: str, current: str, first: str, second: str) -> None:
    idx = frame.index[frame[column].eq(current)]
    frame.loc[idx[: len(idx) // 2], column] = first
    frame.loc[idx[len(idx) // 2 :], column] = second


def test_historical_franchise_codes_are_not_dropped():
    """LA, OAK, SD and STL are the codes nflverse used before the current ids."""
    harness = _load_harness()
    feat = pd.read_csv(FEATURES)
    hold = feat[feat["season"].between(2024, 2025)].copy()
    raw = hold.copy()
    _relabel_half(raw, "home_team", "LAR", "LA", "STL")
    _relabel_half(raw, "away_team", "LAR", "LA", "STL")
    raw.loc[raw["home_team"].eq("LV"), "home_team"] = "OAK"
    raw.loc[raw["away_team"].eq("LV"), "away_team"] = "OAK"
    raw.loc[raw["home_team"].eq("LAC"), "home_team"] = "SD"
    raw.loc[raw["away_team"].eq("LAC"), "away_team"] = "SD"
    used = set(raw["home_team"]) | set(raw["away_team"])
    assert {"LA", "STL", "OAK", "SD"} <= used
    target = _target_from(raw)
    hist = target[["game_id", "home_team", "away_team", "date"]].copy()
    out = harness.predict(hist, target)
    assert len(out) == len(target)
    expected = pd.read_csv(PREDS).set_index("game_id").loc[raw["game_id"].tolist(), "margin"]
    assert out["pred_margin"].notna().all()
    assert out["pred_margin"].to_numpy() == pytest.approx(expected.to_numpy(), abs=1e-6)


def test_unmatched_game_raises_instead_of_dropping():
    harness = _load_harness()
    target = pd.DataFrame({
        "game_id": ["missing-1"],
        "home_team": ["ZZZ"],
        "away_team": ["KC"],
        "date": ["2024-09-05"],
    })
    with pytest.raises(RuntimeError, match="no prior row"):
        harness.predict(target, target)


def test_harness_ignores_grading_columns():
    harness = _load_harness()
    preds = pd.read_csv(PREDS)
    row = preds[preds["season"] == 2024].iloc[[0]]
    bare = row.rename(columns={"gameday": "date"})[
        ["game_id", "home_team", "away_team", "date"]
    ]
    graded = bare.copy()
    graded["spread_line"] = [-6.5]
    graded["total_line"] = [47.5]
    graded["home_moneyline"] = [-180]
    a = harness.predict(bare, bare)
    b = harness.predict(graded, graded)
    assert a["pred_margin"].iloc[0] == pytest.approx(b["pred_margin"].iloc[0])
    assert a["pred_total"].iloc[0] == pytest.approx(b["pred_total"].iloc[0])
