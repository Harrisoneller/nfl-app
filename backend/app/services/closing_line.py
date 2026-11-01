"""One sign for closing lines.

nflverse ``spread_line`` is the home team's margin at the close. Positive
means the home team is favored by that many points. Elo ``pred_spread`` is
the sportsbook number: negative means the home team is favored.

Every comparison in the backtest uses home-margin points. Convert a
sportsbook home spread with ``home_margin_from_sportsbook``. An nflverse
``spread_line`` is already a home margin, so it is stored and graded as-is.
"""
from __future__ import annotations

WON = "won"
LOST = "lost"
PUSH = "push"


def home_margin_from_nflverse(spread_line: float) -> float:
    """nflverse close is already the home margin."""
    return float(spread_line)


def home_margin_from_sportsbook(home_spread: float) -> float:
    """Sportsbook home spread is the negation of the home margin."""
    return -float(home_spread)


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
