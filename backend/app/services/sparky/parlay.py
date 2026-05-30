"""Three-leg parlay generation + composite ranking (SOW 1, "Parlay Ranking").

Given exactly three games, there are 2**3 = 8 ways to pick a winner in each
(home or away). We price and score all eight, then rank by a *composite* score
rather than raw odds, per the spec:

    composite = confidence x signal_alignment x underdog_balance x implied_edge

so the top parlay is the one with the best blend of conviction, market-signal
support, a sensible favorite/underdog mix, and genuine model edge over the
vig-included parlay price — not simply the longest payout.

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
    win_prob: float
    confidence: float
    is_underdog: bool

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
        }


@dataclass
class RankedParlay:
    rank: int
    legs: list[Leg]
    parlay_odds_american: int
    parlay_odds_decimal: float
    implied_prob: float          # vig-included, from the parlay price
    combined_win_prob: float     # model: product of leg win probs
    underdog_count: int
    confidence_score: float      # mean leg confidence (0-100)
    signal_alignment: float      # 0-1
    composite_score: float       # 0-100 (scaled)
    edge: float                  # combined_win_prob - implied_prob
    explanation: str = ""

    def as_dict(self) -> dict:
        return {
            "rank": self.rank,
            "legs": [leg.as_dict() for leg in self.legs],
            "parlay_odds_american": self.parlay_odds_american,
            "parlay_odds_decimal": round(self.parlay_odds_decimal, 3),
            "implied_prob": round(self.implied_prob, 4),
            "combined_win_prob": round(self.combined_win_prob, 4),
            "underdog_count": self.underdog_count,
            "confidence_score": round(self.confidence_score, 1),
            "signal_alignment": round(self.signal_alignment, 3),
            "composite_score": round(self.composite_score, 1),
            "edge": round(self.edge, 4),
            "explanation": self.explanation,
        }


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
    return Leg(
        event_id=g.event_id,
        side=side,
        team_id=team_id,
        opponent_id=opp_id,
        price_american=_price_for_side(g, side, side_prob),
        win_prob=side_prob,
        confidence=conf,
        is_underdog=(side != g.favorite),
    )


def _underdog_balance(underdog_count: int) -> float:
    """Reward a sensible favorite/dog mix; all-chalk pays little, all-dogs is wild."""
    return {0: 0.80, 1: 1.0, 2: 0.90, 3: 0.60}.get(underdog_count, 0.7)


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


def _edge_factor(edge: float) -> float:
    """Map model edge over the parlay's implied prob into a 0.1-1.0 multiplier."""
    return clamp(0.5 + 6.0 * edge, 0.1, 1.0)


def generate_parlays(games: list[GameForParlay]) -> list[RankedParlay]:
    """Rank all 8 three-leg combinations by composite score (best first)."""
    if len(games) != 3:
        raise ValueError("A parlay requires exactly 3 games")

    parlays: list[RankedParlay] = []
    for combo in product(_SIDES, repeat=3):  # 8 combinations
        legs = [_build_leg(g, side) for g, side in zip(games, combo)]
        prices = [leg.price_american for leg in legs]

        dec = odds_math.parlay_decimal(prices)
        amer = odds_math.decimal_to_american(dec)
        implied = odds_math.decimal_to_implied(dec)
        combined_true = odds_math.combined_true_prob([leg.win_prob for leg in legs])
        underdog_count = sum(1 for leg in legs if leg.is_underdog)

        conf_mean = sum(leg.confidence for leg in legs) / 3.0
        alignment = _signal_alignment(legs, games)
        balance = _underdog_balance(underdog_count)
        edge = combined_true - implied
        edge_f = _edge_factor(edge)

        # Composite per spec, scaled to a 0-100 display number.
        composite = (conf_mean / 100.0) * alignment * balance * edge_f * 100.0

        parlays.append(RankedParlay(
            rank=0,
            legs=legs,
            parlay_odds_american=amer,
            parlay_odds_decimal=dec,
            implied_prob=implied,
            combined_win_prob=combined_true,
            underdog_count=underdog_count,
            confidence_score=conf_mean,
            signal_alignment=alignment,
            composite_score=composite,
            edge=edge,
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
    mix = (
        "all favorites" if p.underdog_count == 0
        else f"{p.underdog_count} underdog{'s' if p.underdog_count != 1 else ''}"
    )
    ev = "positive" if p.edge > 0 else "negative"
    return (
        f"{' + '.join(picks)} at {p.parlay_odds_american:+d} "
        f"({p.combined_win_prob * 100:.1f}% model hit). {mix.capitalize()}, "
        f"{ev} model edge vs the {p.implied_prob * 100:.1f}% the price implies; "
        f"composite {p.composite_score:.0f}/100."
    )
