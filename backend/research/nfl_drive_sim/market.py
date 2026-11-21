"""Drop market columns before any research frame is saved or returned.

The names follow the single-feature study's market tokens, plus the nflverse
schedule and play-by-play line columns. A yard line is not a market line:
bare ``line`` is not a token.
"""

from __future__ import annotations

import pandas as pd

MARKET_NAME_PARTS = (
    "spread",
    "moneyline",
    "money_line",
    "odds",
    "total_line",
    "closing_line",
    "opener",
    "vegas",
    "kalshi",
    "vig",
    "juice",
    "implied",
    "over_under",
    "price",
)


class MarketColumnError(ValueError):
    """A line, price, or other market column reached a research frame."""


def is_market_column(name: str) -> bool:
    lowered = str(name).lower()
    return any(part in lowered for part in MARKET_NAME_PARTS)


def market_columns(columns: list[str] | pd.Index) -> list[str]:
    return [str(column) for column in columns if is_market_column(str(column))]


def assert_no_market_columns(frame: pd.DataFrame, label: str = "frame") -> None:
    found = market_columns(frame.columns)
    if found:
        raise MarketColumnError(f"{label} has market columns: {found}")


def strip_market_columns(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Return a copy without market columns, and the names that were removed."""
    found = market_columns(frame.columns)
    if not found:
        return frame.copy(), []
    return frame.drop(columns=found).copy(), found
