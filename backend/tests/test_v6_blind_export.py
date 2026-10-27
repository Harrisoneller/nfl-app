"""The frozen export is the model that was walk-forward fit."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from app.services import v6_blind as v6

DESIGN = Path(__file__).resolve().parent / "fixtures" / "v6_blind_design.csv"
EXPORT = Path(__file__).resolve().parents[1] / "app" / "services" / "v6_blind_export.json"


def test_refit_matches_frozen_export():
    design = pd.read_csv(DESIGN)
    v6.assert_blind_columns(design.columns)
    export = json.loads(EXPORT.read_text())
    assert v6.fit_margin(design) == export["margin_coefs"]
    assert v6.fit_total(design) == export["total_coefs"]
    for season in range(2016, 2024):
        part = design[design["season"] < season]
        assert v6.fit_margin(part) == export["by_season"][str(season)]["margin"]
        assert v6.fit_total(part) == export["by_season"][str(season)]["total"]
    for season in ("2024", "2025", "2026"):
        assert export["by_season"][season]["margin"] == export["margin_coefs"]
        assert export["by_season"][season]["total"] == export["total_coefs"]


def test_design_has_no_market_column():
    header = DESIGN.read_text().splitlines()[0].lower()
    for banned in ("spread", "moneyline", "kalshi", "adp", "total_line", "mkt"):
        assert banned not in header
