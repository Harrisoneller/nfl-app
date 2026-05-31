"""N-leg parlay generation + composite ranking (SOW 1, "Parlay Ranking").

Given N games (2..8), there are 2**N ways to pick a winner in each (home or
away). We price and score every combination, then rank by a *composite* score
rather than raw odds, per the spec:

    composite = confidence × signal_alignment × underdog_balance × value_factor

so the top parlay is the one with the best blend of conviction, market-signal
support, a sensible favorite/underdog mix, and genuine model edge over the
vig-included parlay price — not simply the longest payout.

Value transparency
------------------
Beyond the composite, each leg and each parlay carries explicit value metrics
so a user can answer "is this a +EV play?" directly:

  - per-leg ``edge``  : leg_win_prob − leg_market_implied (vig included)
  - per-leg ``expected_value`` : EV per 1 unit staked at the leg's price
  - parlay ``edge``  : combined_win_prob − parlay_implied
  - parlay ``expected_value`` : EV per 1 unit staked on the parlay
  - parlay ``is_value`` : True if expected_value > 0
  - parlay ``kelly_fraction`` : capped Kelly stake suggestion (0 when -EV)

The composite's ``value_factor`` is asymmetric — it rewards +EV more sharply
than it punishes -EV — so genuinely strong-value parlays climb the rankings.

Pure module; the service supplies the per-game inputs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product

from . import odds_math
from .confidence import _base_confidence, _signal_delta  # reuse the same scoring
from .odds_math import clamp
from .signals import Signal

_SIDES = ("home", "away")
MIN_LEGS = 2
MAX_LEGS = 8


@dataclass
class GameForParlay:
    """Everything the parlay engine needs about one game (both sides priced)."""

    event_id: str
    home_id: str | None
    away_id: str | None
    home_ml: int | None
    away_ml: int | None
    home_prob: float                 # ensemble home win prob
    favorite: str                    # 'home' | 'away'
    signals: list[Signal] = field(default_factory=list)
    label: str = ""                  # e.g. "KC @ BUF" for explanations


@dataclass
class Leg:
    event_id: str
    side: str
    team_id: str | None
    opponent_id: str | None
    price_american: int
    win_prob: float                  # ensemble probability for this side
    confidence: float                # 0-100
    is_underdog: bool
    # Value transparency per leg.
    market_implied: float            # vig-included implied prob at the leg's price
    edge: float                      # win_prob - market_implied
    expected_value: float            # EV per 1 unit at this price

    def as_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "side": self.side,
            "team_id": self.team_id,
            "opponent_id": self.opponent_id,
            "price_american": self.price_american,
            "win_prob": round(self.win_prob, 4),
            "confidence": round(self.confidence, 1),
            "is_underdog": self.is_underdog,
            "market_implied": round(self.market_implied, 4),
            "edge": round(self.edge, 4),
            "expected_value": round(self.expected_value, 4),
            "is_value": self.expected_value > 0,
        }


@dataclass
class RankedParlay:
    rank: int
    legs: list[Leg]
    n_legs: int
    parlay_odds_american: int
    parlay_odds_decimal: float
    implied_prob: float              # vig-included, from the parlay price
    combined_win_prob: float         # model: product of leg win probs
    underdog_count: int
    confidence_score: float          # mean leg confidence (0-100)
    signal_alignment: float          # 0-1
    composite_score: float           # 0-100 (scaled)
    edge: float                      # combined_win_prob - implied_prob
    expected_value: float            # EV per 1 unit staked
    is_value: bool                   # expected_value > 0
    kelly_fraction: float            # capped Kelly stake fraction (0 when -EV)
    explanation: str = ""

    def as_dict(self) -> dict:
        return {
            "rank": self.rank,
            "legs": [leg.as_dict() for leg in self.legs],
            "n_legs": self.n_legs,
            "parlay_odds_american": self.parlay_odds_american,
            "parlay_odds_decimal": round(self.parlay_odds_decimal, 3),
            "implied_prob": round(self.implied_prob, 4),
            "combined_win_prob": round(self.combined_win_prob, 4),
            "underdog_count": self.underdog_count,
            "confidence_score": round(self.confidence_score, 1),
            "signal_alignment": round(self.signal_alignment, 3),
            "composite_score": round(self.composite_score, 1),
            "edge": round(self.edge, 4),
            "expected_value": round(self.expected_value, 4),
            "is_value": self.is_value,
            "kelly_fraction": round(self.kelly_fraction, 4),
            "explanation": self.explanation,
        }


# --------------------------------------------------------------------------- #
# Per-leg construction
# --------------------------------------------------------------------------- #


def _price_for_side(g: GameForParlay, side: str, side_prob: float) -> int:
    """The book's american price for a side, or a fair fallback if absent."""
    price = g.home_ml if side == "home" else g.away_ml
    if price is not None:
        return int(price)
    # Fallback: derive a fair-ish price from probability with a small hold so a
    # missing quote doesn't drop a whole combination.
    p = clamp(side_prob, 0.02, 0.98)
    return odds_math.implied_to_american(clamp(p * 1.03, 0.02, 0.98))


def _build_leg(g: GameForParlay, side: str) -> Leg:
    side_prob = g.home_prob if side == "home" else (1.0 - g.home_prob)
    conf = clamp(_base_confidence(side_prob) + _signal_delta(g.signals, side), 0.0, 100.0)
    team_id = g.home_id if side == "home" else g.away_id
    opp_id = g.away_id if side == "home" else g.home_id
    price = _price_for_side(g, side, side_prob)
    market_imp = odds_math.american_to_implied(price)
    dec = odds_math.american_to_decimal(price)
    ev = odds_math.expected_value(side_prob, dec)
    return Leg(
        event_id=g.event_id,
        side=side,
        team_id=team_id,
        opponent_id=opp_id,
        price_american=price,
        win_prob=side_prob,
        confidence=conf,
        is_underdog=(side != g.favorite),
        market_implied=market_imp,
        edge=side_prob - market_imp,
        expected_value=ev,
    )


# --------------------------------------------------------------------------- #
# Composite components (generalized for any N)
# --------------------------------------------------------------------------- #


def _underdog_balance(underdog_count: int, n_legs: int) -> float:
    """Reward a sensible favorite/dog mix at any N (peaks around 1/3 dogs).

    The spec's three-leg curve was {0:.80, 1:1.0, 2:.90, 3:.60}. We generalize
    so the *extremes* are penalized (all-chalk is a mild penalty for stacked
    vig; all-dog is a bigger penalty for wild variance) and any non-extreme
    mix gets a generous plateau — small parlays (N=2) shouldn't punish "1 dog"
    just because 50% drifts away from the N=3 ideal of 33%.
    """
    if n_legs <= 0:
        return 1.0
    if underdog_count == 0:
        return 0.80          # all-chalk: vig stacks
    if underdog_count == n_legs:
        return 0.60          # all-dog: high variance, usually -EV
    frac = underdog_count / n_legs
    distance = abs(frac - (1.0 / 3.0))
    # Wide plateau (floor 0.85) so any sensibly-mixed parlay beats all-chalk.
    return clamp(1.0 - 0.6 * distance, 0.85, 1.0)


def _signal_alignment(legs: list[Leg], games: list[GameForParlay]) -> float:
    """Mean per-leg signal support for the chosen side (0-1)."""
    by_event = {g.event_id: g for g in games}
    supports: list[float] = []
    for leg in legs:
        g = by_event[leg.event_id]
        # Map the leg's signal delta into 0-1: 0.5 neutral, >0.5 supportive.
        delta = _signal_delta(g.signals, leg.side)
        supports.append(clamp(0.5 + delta / 36.0, 0.0, 1.0))  # 36 = 2 * _MAX_SIGNAL_DELTA
    return sum(supports) / len(supports) if supports else 0.5


def _value_factor(edge: float) -> float:
    """Multiplier in the composite that rewards +EV asymmetrically.

    The user asked Sparky to "appropriately evaluate value picks". The composite
    is a product of four factors in [0, 1], so a small value factor anchors
    the whole ranking on +EV plays. We use a piecewise-linear curve:

      - edge = 0  → 0.55   (neutral plays start in the middle)
      - edge ≥ +0.08 → 1.0  (+8 points of model edge maxes out)
      - edge ≤ -0.06 → 0.10 (anything materially -EV is sharply discounted)

    The positive slope is gentler than the negative slope, so a small +EV play
    earns a meaningful bump while a small -EV play gets penalized harder.
    """
    if edge >= 0:
        return clamp(0.55 + (edge / 0.08) * 0.45, 0.55, 1.0)
    return clamp(0.55 + (edge / 0.06) * 0.45, 0.10, 0.55)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def generate_parlays(games: list[GameForParlay]) -> list[RankedParlay]:
    """Rank every 2**N winner combination for the given N games (best first).

    Raises ``ValueError`` if N is outside [MIN_LEGS, MAX_LEGS]. The default
    "anchor" use case is N=3 (the spec's three-leg parlay), and the service
    persists a recommended 3-leg slate; this engine itself is N-agnostic.
    """
    n = len(games)
    if not MIN_LEGS <= n <= MAX_LEGS:
        raise ValueError(f"A parlay requires {MIN_LEGS}..{MAX_LEGS} legs (got {n})")

    parlays: list[RankedParlay] = []
    for combo in product(_SIDES, repeat=n):  # 2**N combinations
        legs = [_build_leg(g, side) for g, side in zip(games, combo)]
        prices = [leg.price_american for leg in legs]

        dec = odds_math.parlay_decimal(prices)
        amer = odds_math.decimal_to_american(dec)
        implied = odds_math.decimal_to_implied(dec)
        combined_true = odds_math.combined_true_prob([leg.win_prob for leg in legs])
        underdog_count = sum(1 for leg in legs if leg.is_underdog)

        conf_mean = sum(leg.confidence for leg in legs) / n
        alignment = _signal_alignment(legs, games)
        balance = _underdog_balance(underdog_count, n)
        edge = combined_true - implied
        ev = odds_math.expected_value(combined_true, dec)
        kelly = odds_math.kelly_fraction(combined_true, dec)
        value_f = _value_factor(edge)

        # Composite per spec, scaled to a 0-100 display number.
        composite = (conf_mean / 100.0) * alignment * balance * value_f * 100.0

        parlays.append(RankedParlay(
            rank=0,
            legs=legs,
            n_legs=n,
            parlay_odds_american=amer,
            parlay_odds_decimal=dec,
            implied_prob=implied,
            combined_win_prob=combined_true,
            underdog_count=underdog_count,
            confidence_score=conf_mean,
            signal_alignment=alignment,
            composite_score=composite,
            edge=edge,
            expected_value=ev,
            is_value=(ev > 0),
            kelly_fraction=kelly,
        ))

    parlays.sort(key=lambda p: p.composite_score, reverse=True)
    for i, p in enumerate(parlays, start=1):
        p.rank = i
        p.explanation = _explain(p)
    return parlays


def _explain(p: RankedParlay) -> str:
    picks = []
    for leg in p.legs:
        tag = "dog" if leg.is_underdog else "fav"
        picks.append(f"{leg.team_id} ({tag} {leg.win_prob * 100:.0f}%)")
    if p.underdog_count == 0:
        mix = "all favorites"
    elif p.underdog_count == p.n_legs:
        mix = "all underdogs"
    else:
        mix = f"{p.underdog_count} underdog{'s' if p.underdog_count != 1 else ''}"
    value_tag = "+EV" if p.is_value else "-EV"
    ev_pct = p.expected_value * 100.0
    kelly_pct = p.kelly_fraction * 100.0
    kelly_note = f" Kelly suggests {kelly_pct:.1f}% of bankroll." if p.kelly_fraction > 0 else ""
    return (
        f"{' + '.join(picks)} at {p.parlay_odds_american:+d} "
        f"({p.combined_win_prob * 100:.1f}% model hit, {p.n_legs} legs). "
        f"{mix.capitalize()}. {value_tag} {ev_pct:+.1f}% on the dollar vs the "
        f"{p.implied_prob * 100:.1f}% the price implies (composite {p.composite_score:.0f}/100).{kelly_note}"
    )
