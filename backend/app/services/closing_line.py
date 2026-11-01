"""One sign for closing lines.

nflverse ``spread_line`` is the home team's margin at the close. Positive
means the home team is favored by that many points. Elo ``pred_spread`` is
the sportsbook number: negative means the home team is favored.

Every comparison in the backtest uses home-margin points. Convert a
sportsbook home spread with ``home_margin_from_sportsbook``. An nflverse
``spread_line`` is already a home margin, so it is stored and graded as-is.
"""
from __future__ import annotations

import math

WON = "won"
LOST = "lost"
PUSH = "push"


def home_margin_from_nflverse(spread_line: float) -> float:
    """nflverse close is already the home margin."""
    return float(spread_line)


def home_margin_from_sportsbook(home_spread: float) -> float:
    """Sportsbook home spread is the negation of the home margin."""
    return -float(home_spread)


def _finite(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    return number


def closing_numbers_from_nflverse(
    spread_line: float | None,
    total_line: float | None,
) -> tuple[float | None, float | None]:
    """Copy an nflverse schedule row. ``spread_line`` is already a home margin."""
    return _finite(spread_line), _finite(total_line)


def closing_numbers_from_sportsbook(
    home_spread: float | None,
    total: float | None,
) -> tuple[float | None, float | None]:
    """Copy a stored sportsbook number. ``home_spread`` is negative when home is favored."""
    home = _finite(home_spread)
    margin = None if home is None else home_margin_from_sportsbook(home)
    return margin, _finite(total)


def selection_handicap(closing_home_margin: float, *, selection_is_home: bool) -> float:
    """Sportsbook handicap for one side of the stored home margin.

    A home margin of +4 (home favored by 4) is home -4 and away +4.
    """
    margin = float(closing_home_margin)
    return -margin if selection_is_home else margin


def spread_clv(your_handicap: float, closing_handicap: float) -> float:
    """Points of closing-line value. Positive means your number is better.

    Same rule as ``bets.grading.clv_spread``: a more positive handicap is
    always better for the side that holds it.
    """
    return float(your_handicap) - float(closing_handicap)


def total_clv(side: str, your_total: float, closing_total: float) -> float:
    """Points of total CLV. An over wants a lower number; an under wants a higher one."""
    if side.lower() == "over":
        return float(closing_total) - float(your_total)
    return float(your_total) - float(closing_total)


def grade_stored_close(
    *,
    closing_spread: float | None,
    closing_total: float | None,
    model_margin: float | None,
    model_total: float | None,
    home_score: int,
    away_score: int,
) -> dict[str, dict | None]:
    """Grade ATS and CLV from the stored close only.

    A missing close is left ungraded. This function does not read a schedule
    or an odds feed.
    """
    actual_margin = int(home_score) - int(away_score)
    actual_total = int(home_score) + int(away_score)
    spread: dict | None = None
    if closing_spread is not None and model_margin is not None:
        result = ats_grade(model_margin, closing_spread, actual_margin)
        clv = None
        if result is not None and result != PUSH and model_margin != closing_spread:
            picks_home = float(model_margin) > float(closing_spread)
            clv = spread_clv(
                selection_handicap(model_margin, selection_is_home=picks_home),
                selection_handicap(closing_spread, selection_is_home=picks_home),
            )
        spread = {"result": result, "clv": clv}
    total: dict | None = None
    if closing_total is not None and model_total is not None:
        if float(model_total) == float(closing_total):
            total = {"side": None, "result": None, "clv": None}
        else:
            side = "over" if float(model_total) > float(closing_total) else "under"
            if actual_total == float(closing_total):
                result = PUSH
            elif (actual_total > float(closing_total)) == (side == "over"):
                result = WON
            else:
                result = LOST
            total = {
                "side": side,
                "result": result,
                "clv": total_clv(side, model_total, closing_total),
            }
    return {"spread": spread, "total": total}


def ats_grade(
    model_margin: float,
    closing_home_margin: float,
    actual_margin: float,
) -> str | None:
    """Grade one spread against the close. All three values are home margins.

    Positive means the home side. The model takes home when its margin is
    larger than the close, and away when it is smaller. An exact match is
    not a bet (``None``). A final margin that lands on the number is a push.
    """
    model = float(model_margin)
    close = float(closing_home_margin)
    actual = float(actual_margin)
    if actual == close:
        return PUSH
    if model == close:
        return None
    model_picks_home = model > close
    home_covered = actual > close
    return WON if model_picks_home == home_covered else LOST
