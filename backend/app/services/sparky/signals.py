"""Market-signal taxonomy + detection framework (SOW 1, "Required Signal Framework").

A *signal* is a named, explainable market pattern with a direction (which side it
supports) and a magnitude (how strong). The confidence model (``confidence.py``)
turns the set of signals on a game into a boost/penalty on the base win prob.

Data honesty note
-----------------
Several textbook signals (reverse line movement, resistance) are classically
defined against *public betting percentages* (tickets vs. handle). The free Odds
API does not expose that, so where a signal would need public money we approximate
it from observable quantities only — line movement direction/magnitude and
cross-book dispersion — and say so in the explanation. Each detector documents its
proxy. This keeps the engine fully functional on free data while being upfront
about the approximation; wiring in a public-money feed later only sharpens the
same signals without changing the interface.

All functions here are pure.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MovementPoint:
    """Consensus state at one captured snapshot."""

    label: str                      # T1/T2/T3/T4
    minutes_to_kickoff: float | None  # >0 before kickoff
    home_prob: float                # de-vigged consensus home win prob at this snapshot
    home_ml: int | None = None
    away_ml: int | None = None


@dataclass
class SignalInput:
    """Everything a detector might need about one game, computed by the service."""

    home_team_id: str | None
    away_team_id: str | None
    favorite: str | None                 # 'home' | 'away' | None
    home_market_prob: float              # de-vigged consensus
    away_market_prob: float
    home_ml: int | None = None
    away_ml: int | None = None
    spread_home: float | None = None     # negative = home favored
    total: float | None = None
    book_count: int = 0
    book_home_probs: list[float] = field(default_factory=list)  # per-book de-vigged home prob
    movement: list[MovementPoint] = field(default_factory=list)  # chronological
    model_home_prob: float | None = None   # ensemble/Elo base (no signals yet)
    model_away_prob: float | None = None

    # --- NFL-specific context (optional, populated by the service layer) ---
    home_rest_days: float | None = None   # days since the home team's last game (None = unknown)
    away_rest_days: float | None = None   # days since the away team's last game
    is_divisional: bool = False           # true if same conference + same division



@dataclass(frozen=True)
class Signal:
    key: str
    label: str
    # 'home' | 'away' | 'game' — the side this signal supports ('game' = neutral/variance)
    side: str
    # 'bullish' supports a side | 'warning' adds risk/variance | 'info' display-only
    severity: str
    magnitude: float           # 0..1 strength
    # Confidence points at magnitude == 1. ALWAYS >= 0; the sign of the effect is
    # derived from severity + side in confidence.py (a bullish signal for the
    # predicted side adds, for the opponent subtracts; a warning always subtracts;
    # an info signal never moves the score). Keeping weights non-negative avoids
    # double-negation bugs.
    weight: float
    explanation: str

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "side": self.side,
            "severity": self.severity,
            "magnitude": round(self.magnitude, 3),
            "weight": self.weight,
            "explanation": self.explanation,
        }


# Human-readable taxonomy for the UI's "signal glossary" / admin view.
SIGNAL_DEFINITIONS: dict[str, dict[str, str]] = {
    "steam_move": {"label": "Steam Move", "definition": "Fast, one-directional line movement confirmed across books."},
    "reverse_line_movement": {"label": "Reverse Line Movement", "definition": "Line moved toward the underdog even though the favorite is still favored — a sharp-money proxy."},
    "late_movement": {"label": "Late Movement", "definition": "Most of the movement happened in the snapshot closest to kickoff."},
    "market_compression": {"label": "Market Compression", "definition": "Books converged / the line tightened as the market sharpened."},
    "false_stability": {"label": "False Stability", "definition": "Consensus looks flat but books disagree — stability is illusory."},
    "resistance": {"label": "Resistance", "definition": "Line held firm despite time passing — market conviction on the number."},
    "anchor_favorite": {"label": "Anchor Favorite", "definition": "Strong favorite with tight multi-book agreement — a parlay anchor."},
    "coin_flip": {"label": "Coin Flip", "definition": "Near pick'em; little market edge either way."},
    "high_variance": {"label": "High-Variance Game", "definition": "Books disagree and/or model and market diverge — wide outcome range."},
    "multibook_confirmation": {"label": "Multi-book Confirmation", "definition": "All books agree on the favorite and the price."},
    "multibook_disagreement": {"label": "Multi-book Disagreement", "definition": "Books split on the side or price."},
    "favorite_weakening": {"label": "Favorite Weakening", "definition": "The favorite's win probability eroded over time."},
    "underdog_strengthening": {"label": "Underdog Strengthening", "definition": "Money came in on the underdog over time."},
    "trap_risk": {"label": "Trap Risk", "definition": "A heavy favorite the model rates well below the market — a possible trap."},
    "upset_pressure": {"label": "Upset Pressure", "definition": "The model gives the underdog a materially better shot than the market."},
    # NFL-specific (sport module)
    "short_rest": {"label": "Short Rest", "definition": "Team is playing on short rest (typically Thu-Sun or back-to-back road). Historically disadvantaged."},
    "rest_advantage": {"label": "Rest Advantage", "definition": "Team has meaningfully more rest (or coming off bye) than its opponent — situational edge."},
    "divisional_matchup": {"label": "Divisional Matchup", "definition": "Intra-division game. Higher variance, tighter spreads, and 'any given Sunday' outcomes are common."},
}


# --------------------------------------------------------------------------- #
# Tunables (documented; conservative defaults)
# --------------------------------------------------------------------------- #

_ANCHOR_PROB = 0.72          # favorite prob to be "strong"
_COIN_FLIP_BAND = 0.06       # |home_prob - 0.5| <= this => coin flip
_STEAM_DELTA = 0.04          # total de-vigged prob move to count as steam
_LATE_FRACTION = 0.6         # fraction of total move concentrated late
_DISPERSION_TIGHT = 0.015    # stdev of book home prob considered "tight"
_DISPERSION_WIDE = 0.05      # stdev considered "wide" (disagreement)
_MODEL_DIVERGENCE = 0.07     # |model - market| home prob gap that's material

# NFL-specific tunables
_SHORT_REST_CUTOFF = 6.5       # <= this many days rest → short week warning
_REST_ADVANTAGE_MIN = 3.0      # days more rest than opponent to count as advantage
_REST_BYE_THRESHOLD = 10.0     # days rest → treat as bye / extra rest (stronger boost)


def _safe_stdev(xs: list[float]) -> float:
    return statistics.pstdev(xs) if len(xs) >= 2 else 0.0


def _fav_side(inp: SignalInput) -> str:
    if inp.favorite in ("home", "away"):
        return inp.favorite
    return "home" if inp.home_market_prob >= inp.away_market_prob else "away"


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #


def detect_signals(inp: SignalInput) -> list[Signal]:
    """Run every detector and return the signals that fired, strongest first."""
    out: list[Signal] = []
    fav = _fav_side(inp)
    dog = "away" if fav == "home" else "home"
    fav_prob = inp.home_market_prob if fav == "home" else inp.away_market_prob
    dispersion = _safe_stdev(inp.book_home_probs)

    out += _static_signals(inp, fav, dog, fav_prob, dispersion)
    out += _movement_signals(inp, fav, dog)
    out += _model_vs_market_signals(inp, fav, dog, fav_prob)
    out += _nfl_context_signals(inp, fav, dog)

    out.sort(key=lambda s: s.magnitude * abs(s.weight), reverse=True)
    return out


def _static_signals(inp: SignalInput, fav: str, dog: str, fav_prob: float, dispersion: float) -> list[Signal]:
    sigs: list[Signal] = []

    # Coin flip — near pick'em.
    gap = abs(inp.home_market_prob - 0.5)
    if gap <= _COIN_FLIP_BAND:
        mag = 1.0 - gap / _COIN_FLIP_BAND
        sigs.append(Signal(
            "coin_flip", "Coin Flip", "game", "warning", mag, 8.0,
            f"Market has this near a pick'em ({fav_prob * 100:.0f}% favorite) — low edge either way.",
        ))

    # Anchor favorite — strong fav + tight book agreement.
    if fav_prob >= _ANCHOR_PROB:
        tight = inp.book_count >= 2 and dispersion <= _DISPERSION_TIGHT
        strength = (fav_prob - _ANCHOR_PROB) / (0.98 - _ANCHOR_PROB)
        mag = min(1.0, max(0.2, strength)) * (1.0 if tight else 0.7)
        conf_note = "with tight multi-book agreement" if tight else "though books vary on the price"
        sigs.append(Signal(
            "anchor_favorite", "Anchor Favorite", fav, "bullish", mag, 10.0,
            f"{fav_prob * 100:.0f}% favorite {conf_note} — a candidate parlay anchor.",
        ))

    # Multi-book confirmation vs disagreement.
    if inp.book_count >= 3:
        if dispersion <= _DISPERSION_TIGHT:
            sigs.append(Signal(
                "multibook_confirmation", "Multi-book Confirmation", fav, "bullish",
                min(1.0, (_DISPERSION_TIGHT - dispersion) / _DISPERSION_TIGHT + 0.4), 5.0,
                f"All {inp.book_count} books line up on {fav.upper()} — strong consensus.",
            ))
        elif dispersion >= _DISPERSION_WIDE:
            sigs.append(Signal(
                "multibook_disagreement", "Multi-book Disagreement", "game", "warning",
                min(1.0, dispersion / (_DISPERSION_WIDE * 2)), 5.0,
                f"Books disagree (±{dispersion * 100:.1f}pts implied) — the market hasn't settled.",
            ))

    # High variance — wide book dispersion OR a shootout total.
    variance_mag = 0.0
    reasons = []
    if dispersion >= _DISPERSION_WIDE:
        variance_mag = max(variance_mag, min(1.0, dispersion / (_DISPERSION_WIDE * 2)))
        reasons.append("books disagree")
    if inp.total is not None and inp.total >= 51.0:
        variance_mag = max(variance_mag, min(1.0, (inp.total - 51.0) / 10.0 + 0.3))
        reasons.append(f"high total ({inp.total:.0f})")
    if variance_mag > 0:
        sigs.append(Signal(
            "high_variance", "High-Variance Game", "game", "warning", variance_mag, 6.0,
            "Wide outcome range — " + ", ".join(reasons) + ".",
        ))

    return sigs


def _movement_signals(inp: SignalInput, fav: str, dog: str) -> list[Signal]:
    """Signals that require >= 2 snapshots of line-movement history."""
    sigs: list[Signal] = []
    pts = [p for p in inp.movement if p is not None]
    if len(pts) < 2:
        return sigs

    first, last = pts[0], pts[-1]
    total_delta = last.home_prob - first.home_prob          # + => moved toward home
    abs_total = abs(total_delta)
    # Direction the line moved, expressed as a side.
    moved_toward = "home" if total_delta > 0 else "away"

    # Steam move — large total move in one direction.
    if abs_total >= _STEAM_DELTA:
        mag = min(1.0, abs_total / (_STEAM_DELTA * 3))
        sigs.append(Signal(
            "steam_move", "Steam Move", moved_toward, "bullish", mag, 8.0,
            f"Line moved {abs_total * 100:.1f}pts toward {moved_toward.upper()} across the window — momentum.",
        ))

    # Late movement — most of the move came in the final leg.
    if len(pts) >= 3 and abs_total >= _STEAM_DELTA * 0.5:
        late_delta = last.home_prob - pts[-2].home_prob
        if abs_total > 0 and abs(late_delta) / abs_total >= _LATE_FRACTION:
            late_side = "home" if late_delta > 0 else "away"
            sigs.append(Signal(
                "late_movement", "Late Movement", late_side, "bullish",
                min(1.0, abs(late_delta) / (_STEAM_DELTA * 2)), 6.0,
                f"{abs(late_delta) * 100:.1f}pts of the move hit late toward {late_side.upper()} — fresh money near kickoff.",
            ))

    # Favorite weakening / underdog strengthening — line drifted off the favorite.
    fav_prob_delta = total_delta if fav == "home" else -total_delta  # + => fav got stronger
    if fav_prob_delta <= -_STEAM_DELTA * 0.6:
        mag = min(1.0, abs(fav_prob_delta) / (_STEAM_DELTA * 2))
        # Display-only counterpart (info, weight 0) so the card narrates both
        # sides of the same move without double-counting it in the math.
        sigs.append(Signal(
            "favorite_weakening", "Favorite Weakening", fav, "info", mag, 0.0,
            f"The {fav.upper()} favorite lost {abs(fav_prob_delta) * 100:.1f}pts of implied probability over time.",
        ))
        sigs.append(Signal(
            "underdog_strengthening", "Underdog Strengthening", dog, "bullish", mag, 5.0,
            f"Money has come in on the {dog.upper()} underdog — the price firmed up.",
        ))

    # Reverse line movement (proxy) — moved toward the dog while fav stays favored.
    if moved_toward == dog and abs_total >= _STEAM_DELTA * 0.75:
        mag = min(1.0, abs_total / (_STEAM_DELTA * 2.5))
        sigs.append(Signal(
            "reverse_line_movement", "Reverse Line Movement", dog, "bullish", mag, 6.0,
            f"Line drifted to the {dog.upper()} underdog despite it staying the dog — a sharp-money proxy "
            "(approximated from movement; no public-ticket feed).",
        ))

    # Resistance vs. false stability — both start from "flat line".
    if abs_total < _STEAM_DELTA * 0.4:
        dispersion = _safe_stdev(inp.book_home_probs)
        if dispersion >= _DISPERSION_WIDE:
            sigs.append(Signal(
                "false_stability", "False Stability", "game", "warning",
                min(1.0, dispersion / (_DISPERSION_WIDE * 2)), 4.0,
                "Consensus looks flat, but books disagree underneath — the calm is misleading.",
            ))
        else:
            fav_prob = inp.home_market_prob if fav == "home" else inp.away_market_prob
            if fav_prob >= 0.6:
                sigs.append(Signal(
                    "resistance", "Resistance", fav, "bullish",
                    min(1.0, (fav_prob - 0.6) / 0.3 + 0.3), 4.0,
                    f"The line on {fav.upper()} held firm through the window — market conviction on the number.",
                ))

    # Market compression — books converged from open to now.
    if len(pts) >= 2 and inp.book_count >= 3:
        # We only have current per-book dispersion; treat a currently-tight book
        # set after meaningful movement as compression toward consensus.
        dispersion = _safe_stdev(inp.book_home_probs)
        if dispersion <= _DISPERSION_TIGHT and abs_total >= _STEAM_DELTA * 0.5:
            sigs.append(Signal(
                "market_compression", "Market Compression", moved_toward, "bullish",
                min(1.0, abs_total / (_STEAM_DELTA * 2)), 4.0,
                f"Books tightened around {moved_toward.upper()} after the move — the market sharpened.",
            ))

    return sigs


def _model_vs_market_signals(inp: SignalInput, fav: str, dog: str, fav_prob: float) -> list[Signal]:
    """Trap risk and upset pressure compare our model to the market."""
    sigs: list[Signal] = []
    if inp.model_home_prob is None:
        return sigs

    model_home = inp.model_home_prob
    market_home = inp.home_market_prob

    # Trap risk — heavy fav the model rates well below the market.
    if fav_prob >= 0.68:
        fav_model = model_home if fav == "home" else (1.0 - model_home)
        fav_market = market_home if fav == "home" else (1.0 - market_home)
        gap = fav_market - fav_model
        if gap >= _MODEL_DIVERGENCE:
            sigs.append(Signal(
                "trap_risk", "Trap Risk", dog, "warning", min(1.0, gap / 0.2), 7.0,
                f"Market loves {fav.upper()} ({fav_market * 100:.0f}%) but the model only gives "
                f"{fav_model * 100:.0f}% — possible trap on the chalk.",
            ))

    # Upset pressure — model materially higher on the dog than the market.
    dog_model = (1.0 - model_home) if dog == "away" else model_home
    dog_market = inp.away_market_prob if dog == "away" else inp.home_market_prob
    dog_gap = dog_model - dog_market
    if dog_gap >= _MODEL_DIVERGENCE:
        sigs.append(Signal(
            "upset_pressure", "Upset Pressure", dog, "bullish", min(1.0, dog_gap / 0.2), 7.0,
            f"Model gives {dog.upper()} {dog_model * 100:.0f}% vs the market's {dog_market * 100:.0f}% — live upset value.",
        ))

    return sigs


# --------------------------------------------------------------------------- #
# NFL-specific context signals (the "sport module" per the Sparky spec v1.0)
# These use optional fields that the service layer populates from the schedule.
#
# Currently implemented:
#   - short_rest / rest_advantage (days since last final game, with bye awareness)
#   - divisional_matchup (same conference + division)
#
# Future easy extensions in this same pattern: revenge, travel distance,
# prime-time letdown, QB rest, etc.
# --------------------------------------------------------------------------- #


def _nfl_context_signals(inp: SignalInput, fav: str, dog: str) -> list[Signal]:
    """Rest / short week and divisional matchup signals."""
    sigs: list[Signal] = []

    h_rest = inp.home_rest_days
    a_rest = inp.away_rest_days
    div = inp.is_divisional

    # Divisional matchup — variance + familiarity effect
    if div:
        sigs.append(Signal(
            "divisional_matchup", "Divisional Matchup", "game", "info", 0.7, 3.0,
            "Intra-division game — higher variance and familiarity often produce closer or surprising results.",
        ))

    # Rest / short week logic (only when we have data for both sides)
    if h_rest is not None and a_rest is not None:
        rest_diff = h_rest - a_rest   # positive = home has more rest than away

        # Short rest for home team
        if h_rest <= _SHORT_REST_CUTOFF:
            mag = min(1.0, (_SHORT_REST_CUTOFF - h_rest) / 4.0 + 0.4)
            weight = 9.0 if h_rest < 5.0 else 6.0
            sigs.append(Signal(
                "short_rest", "Short Rest", "home", "warning", mag, weight,
                f"Home team on short rest ({h_rest:.0f} days since last game).",
            ))

        # Short rest for away team (more common on Thursday/road back-to-backs)
        if a_rest <= _SHORT_REST_CUTOFF:
            mag = min(1.0, (_SHORT_REST_CUTOFF - a_rest) / 4.0 + 0.4)
            weight = 9.0 if a_rest < 5.0 else 6.0
            sigs.append(Signal(
                "short_rest", "Short Rest", "away", "warning", mag, weight,
                f"Away team on short rest ({a_rest:.0f} days since last game).",
            ))

        # Clear rest advantage (one side has significantly more rest)
        adv_side = None
        adv_mag = 0.0
        if rest_diff >= _REST_ADVANTAGE_MIN:
            adv_side = "home"
            adv_mag = min(1.0, (rest_diff - _REST_ADVANTAGE_MIN) / 6.0)
        elif rest_diff <= -_REST_ADVANTAGE_MIN:
            adv_side = "away"
            adv_mag = min(1.0, (-rest_diff - _REST_ADVANTAGE_MIN) / 6.0)

        if adv_side:
            # Stronger if it crosses the bye threshold
            if (adv_side == "home" and h_rest >= _REST_BYE_THRESHOLD) or (adv_side == "away" and a_rest >= _REST_BYE_THRESHOLD):
                adv_mag = min(1.0, adv_mag + 0.35)
            sigs.append(Signal(
                "rest_advantage", "Rest Advantage", adv_side, "bullish", adv_mag, 7.5,
                f"{adv_side.capitalize()} has a clear rest edge ({abs(rest_diff):.0f} days).",
            ))

    return sigs
