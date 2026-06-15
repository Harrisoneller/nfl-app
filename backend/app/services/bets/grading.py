"""Pure grading + CLV math for bet settlement.

Deliberately free of any DB / ORM imports so it can be unit-tested in isolation
with plain numbers. ``bet_service`` extracts values off the ORM rows and calls
these; the DB orchestration lives there.

Vocabulary
----------
- A leg result is one of ``"won" | "lost" | "push"`` (or ``None`` = not gradable
  yet, e.g. game not final).
- **CLV (closing line value)** measures whether you got a better number/price
  than the market's closing line. For moneylines it's *price-based* (% better
  than the closing decimal price). For spreads/totals it's *line-based* (points
  better than the closing number), since our snapshots store the spread/total
  *number* but not its per-side price.
"""
from __future__ import annotations

from ..sparky.odds_math import american_to_decimal

WON = "won"
LOST = "lost"
PUSH = "push"


# --------------------------------------------------------------------------- #
# Grading — given a FINAL game's scores, did the selection win/lose/push?
# --------------------------------------------------------------------------- #


def grade_moneyline(
    selection_team_id: str,
    home_team_id: str,
    away_team_id: str,
    home_score: int,
    away_score: int,
) -> str:
    """Straight-up winner. A tie pushes (rare in NFL, but possible)."""
    if home_score == away_score:
        return PUSH
    winner = home_team_id if home_score > away_score else away_team_id
    return WON if selection_team_id == winner else LOST


def grade_spread(
    line: float,
    selection_team_id: str,
    home_team_id: str,
    away_team_id: str,
    home_score: int,
    away_score: int,
) -> str:
    """Spread bet. ``line`` is the selected team's handicap (e.g. -3.5 or +6.5).

    The selection covers when its score plus its handicap exceeds the opponent's.
    A result of exactly 0 after applying the handicap is a push.
    """
    if selection_team_id == home_team_id:
        team_score, opp_score = home_score, away_score
    else:
        team_score, opp_score = away_score, home_score
    margin = (team_score + line) - opp_score
    if margin > 0:
        return WON
    if margin == 0:
        return PUSH
    return LOST


def grade_total(over_under: str, line: float, home_score: int, away_score: int) -> str:
    """Total (over/under). ``over_under`` is ``"over"`` or ``"under"``."""
    combined = home_score + away_score
    if combined == line:
        return PUSH
    went_over = combined > line
    if over_under.lower() == "over":
        return WON if went_over else LOST
    return LOST if went_over else WON


# --------------------------------------------------------------------------- #
# CLV — compare what you got to the closing number/price
# --------------------------------------------------------------------------- #


def clv_moneyline(your_american: int, closing_american: int) -> float:
    """Price-based CLV %: how much better your decimal price is than the close.

    Positive => you locked a better price than the market closed at (good).
    e.g. you took +150 (2.50) and it closed +120 (2.20) -> (2.50/2.20 - 1) ≈ +13.6%.
    """
    your_dec = american_to_decimal(your_american)
    close_dec = american_to_decimal(closing_american)
    return (your_dec / close_dec - 1.0) * 100.0


def clv_spread(your_line: float, closing_line: float) -> float:
    """Line-based CLV (points) for a spread, normalized so positive = better.

    ``line`` is the selection's handicap, so a more positive number is always
    better for the side holding it (you'd rather have +4 than +3, or -2.5 than
    -3.5). So value = your_line - closing_line.
    """
    return your_line - closing_line


def clv_total(over_under: str, your_line: float, closing_line: float) -> float:
    """Line-based CLV (points) for a total, normalized so positive = better.

    An ``over`` wants the lowest possible number; an ``under`` wants the highest.
    """
    if over_under.lower() == "over":
        return closing_line - your_line
    return your_line - closing_line


# --------------------------------------------------------------------------- #
# Parlay settlement
# --------------------------------------------------------------------------- #


def settle_parlay(
    leg_results: list[str],
    leg_decimals: list[float],
    stake_units: float,
) -> tuple[str, float, float]:
    """Roll up leg results into a parlay outcome.

    Standard sportsbook rules:
      - Any LOST leg => the whole parlay loses.
      - PUSH/VOID legs drop out (their decimal becomes 1.0), reducing the parlay
        odds but not killing it.
      - If every remaining leg won => the parlay wins at the reduced odds.

    Returns ``(status, payout_units, result_units)`` where ``payout_units`` is the
    total returned including stake on a win, and ``result_units`` is profit (+) /
    loss (-) / 0 on a full push.
    """
    if any(r == LOST for r in leg_results):
        return LOST, 0.0, -stake_units

    if any(r is None for r in leg_results):  # not fully gradable yet
        return "pending", 0.0, 0.0

    # All legs are won or push. Multiply only the winning legs' decimals.
    eff_decimal = 1.0
    won_any = False
    for result, dec in zip(leg_results, leg_decimals):
        if result == WON:
            eff_decimal *= dec
            won_any = True
        # push/void -> factor of 1.0 (no change)

    if not won_any:
        # Every leg pushed -> stake returned, no profit.
        return PUSH, stake_units, 0.0

    payout = stake_units * eff_decimal
    return WON, payout, payout - stake_units


def settle_straight(
    leg_result: str,
    leg_decimal: float,
    stake_units: float,
) -> tuple[str, float, float]:
    """Single-leg settlement. Returns ``(status, payout_units, result_units)``."""
    if leg_result == LOST:
        return LOST, 0.0, -stake_units
    if leg_result == PUSH:
        return PUSH, stake_units, 0.0
    payout = stake_units * leg_decimal
    return WON, payout, payout - stake_units
