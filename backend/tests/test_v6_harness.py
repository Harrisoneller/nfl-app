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


def test_priors_have_no_market_column():
    header = FEATURES.read_text().splitlines()[0].lower()
    for banned in ("spread", "moneyline", "kalshi", "adp", "total_line", "mkt"):
        assert banned not in header


def test_harness_reproduces_backtest_predictions():
    harness = _load_harness()
    preds = pd.read_csv(PREDS)
    # A slice keeps the test small and still checks both the walk-forward
    # seasons and the frozen holdout.
    sample = pd.concat([
        preds[preds["season"].between(2016, 2023)].iloc[::40],
        preds[preds["season"].between(2024, 2026)],
    ], ignore_index=True)
    hist = sample[["home_team", "away_team", "gameday", "season"]].copy()
    hist = hist.rename(columns={"gameday": "date"})
    target = hist.copy()
    out = harness.predict(hist, target)
    assert list(out.columns) == ["margin", "total"]
    assert out.index.equals(target.index)
    assert out["margin"].to_numpy() == pytest.approx(sample["margin"].to_numpy(), abs=1e-6)
    assert out["total"].to_numpy() == pytest.approx(sample["total"].to_numpy(), abs=1e-6)


def test_harness_rejects_a_market_column():
    harness = _load_harness()
    hist = pd.DataFrame({"home_team": ["KC"], "away_team": ["BAL"], "date": ["2024-09-05"]})
    target = hist.copy()
    target["spread_line"] = [3.0]
    with pytest.raises(ValueError):
        harness.predict(hist, target)
