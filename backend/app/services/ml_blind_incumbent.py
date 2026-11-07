"""Walk-forward stage-2 margin refit for the v5 incumbent.

The primary incumbent refits OLS on seasons strictly before the target season.
Shipped ``MARGIN_COEFS`` are a descriptive replay only. This module does not
read NFL files.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

STAGE2_COLUMNS = ("mkt_d", "epa_d", "pts_d", "st_d", "qb_d", "hfa")


def rows_before_season(frame: pd.DataFrame, season: int) -> pd.DataFrame:
    """Prior-season rows only. The target season is not a fit row."""
    return frame.loc[frame["season"].astype(int) < int(season)].copy()


def refit_stage2_margin(
    frame: pd.DataFrame,
    season: int,
    columns: tuple[str, ...] = STAGE2_COLUMNS,
) -> dict[str, float]:
    """OLS of home margin on the v5 margin features, prior seasons only.

    ``mkt_d``, when included, is the historical market prior from earlier closes.
    The target game's own close is not a column in this fit.
    """
    prior = rows_before_season(frame, season).dropna(subset=[*columns, "result"])
    if len(prior) < len(columns):
        raise ValueError(f"not enough prior rows to refit stage-2 for {season}")
    design = np.column_stack([prior[col].to_numpy(dtype=float) for col in columns])
    target = prior["result"].to_numpy(dtype=float)
    coef, *_ = np.linalg.lstsq(design, target, rcond=None)
    return {name: float(value) for name, value in zip(columns, coef, strict=True)}


def apply_stage2(features: dict[str, float], coefs: dict[str, float]) -> float:
    """Home margin from a refit coefficient vector. ``hfa`` is 0 or 1."""
    return float(sum(float(coefs[name]) * float(features.get(name, 0.0)) for name in coefs))
