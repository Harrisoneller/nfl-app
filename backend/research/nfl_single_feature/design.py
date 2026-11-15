"""Market-blind design guard and ordinary least squares.

A market column is rejected by name and by allowlist. Closing lines may sit
on a frame for a later grade. They are not design columns, and this module
never puts them in ``X``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .specs import MARKET_NAME_PARTS


class DesignError(ValueError):
    """The design is outside the preregistered allowlist."""


def is_market_column(name: str) -> bool:
    lowered = str(name).lower()
    return any(part in lowered for part in MARKET_NAME_PARTS)


def assert_allowlist(columns: tuple[str, ...] | list[str], allowed: tuple[str, ...]) -> None:
    """Raise unless ``columns`` is exactly a subset of ``allowed``, in any order.

    Duplicates raise. Market-like names raise even if a caller appended them
    to ``allowed``.
    """
    if len(set(columns)) != len(columns):
        raise DesignError("duplicate design column")
    extra = [col for col in columns if col not in allowed]
    if extra:
        raise DesignError(f"column is outside the allowlist: {extra}")
    banned = [col for col in columns if is_market_column(col)]
    if banned:
        raise DesignError(f"market column in the design: {banned}")


def assert_single_addition(
    base: tuple[str, ...], feature: str,
) -> tuple[str, ...]:
    """Return the candidate column list: the frozen base plus one feature."""
    if feature in base:
        raise DesignError(f"{feature} is already in the base")
    if is_market_column(feature):
        raise DesignError(f"market column cannot be the added feature: {feature}")
    columns = tuple(base) + (feature,)
    assert_allowlist(columns, columns)
    return columns


def complete_rows(frame: pd.DataFrame, columns: tuple[str, ...] | list[str], target: str) -> pd.DataFrame:
    needed = list(columns) + [target]
    missing = [col for col in needed if col not in frame.columns]
    if missing:
        raise DesignError(f"missing columns: {missing}")
    return frame.dropna(subset=needed).copy()


def fit_ols(
    frame: pd.DataFrame,
    columns: tuple[str, ...] | list[str],
    target: str,
    *,
    intercept: bool,
) -> dict[str, float]:
    """Least squares. ``intercept`` adds a constant. No other column is read."""
    assert_allowlist(columns, tuple(columns))
    data = complete_rows(frame, columns, target)
    width = len(columns) + (1 if intercept else 0)
    if len(data) < width:
        raise DesignError("not enough complete rows to fit stage-2")
    pieces = [data[col].to_numpy(dtype=float) for col in columns]
    if intercept:
        pieces.append(np.ones(len(data)))
    design = np.column_stack(pieces)
    coef, _, _, _ = np.linalg.lstsq(design, data[target].to_numpy(dtype=float), rcond=None)
    out = {col: float(coef[i]) for i, col in enumerate(columns)}
    if intercept:
        out["const"] = float(coef[-1])
    return out


def predict(
    frame: pd.DataFrame,
    coefs: dict[str, float],
    columns: tuple[str, ...] | list[str],
    *,
    intercept: bool,
) -> np.ndarray:
    """Predict. Columns other than ``columns`` are not read."""
    pred = np.zeros(len(frame), dtype=float)
    for col in columns:
        pred = pred + coefs[col] * frame[col].to_numpy(dtype=float)
    if intercept:
        pred = pred + coefs["const"]
    return pred
