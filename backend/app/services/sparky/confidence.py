"""Confidence scoring — the ensemble (SOW 1, "Confidence Scoring").

Per Harrison's decision, Sparky *ensembles* the app's existing Elo/ML win
probability with the de-vigged market probability, then applies the signal
boosts/penalties on top. The output is:

  - ``win_prob``        : ensemble probability for the predicted winner (0-1)
  - ``confidence_score``: 0-100, base 45-95 from win_prob then signal-adjusted
  - ``classification``  : anchor | strong_lean | lean | coin_flip | upset_watch

Why blend in logit space
------------------------
Averaging probabilities directly understates confident agreement (0.9 and 0.85
should stay ~0.88, not get dragged toward 0.5). Blending the log-odds and mapping
back keeps the ensemble well-calibrated and symmetric.

Signal application rule (matches signals.py weights, which are all >= 0):
  - bullish + supports predicted side  -> + magnitude * weight
  - bullish + supports the opponent     -> - magnitude * weight
  - warning (any side)                  -> - magnitude * weight
  - info                                -> 0
The net signal delta is clamped so signals adjust, but never dominate, the
statistical base.
"""
from __future__ import annotations

from dataclasses import dataclass

from .odds_math import clamp, inv_logit, logit
from .signals import Signal

# Ensemble weights for the model (Elo/ML) vs. the de-vigged market.
# The market is sharp, so it gets the larger share; the model contributes
# genuine independent signal (injuries-via-ratings, situational, etc.).
_W_MODEL = 0.45
_W_MARKET = 0.55

# Base-confidence mapping: win_prob 0.50 -> 45, 0.95 -> 95 (clamped).
_CONF_FLOOR = 45.0
_CONF_CEIL = 95.0
_PROB_FLOOR = 0.50
_PROB_CEIL = 0.95

# Signals may move confidence by at most this many points net, either way.
_MAX_SIGNAL_DELTA = 18.0


@dataclass
class GameScore:
    predicted_winner_side: str        # 'home' | 'away'
    win_prob: float                   # ensemble prob for the predicted winner
    home_win_prob: float              # ensemble prob for home (for transparency)
    confidence_score: float           # 0-100
    base_confidence: float            # before signals
    signal_delta: float               # net points signals contributed
    classification: str
    model_prob: float | None          # model prob for predicted winner
    market_prob: float | None         # market prob for predicted winner

    def as_dict(self) -> dict:
        return {
            "predicted_winner_side": self.predicted_winner_side,
            "win_prob": round(self.win_prob, 4),
            "home_win_prob": round(self.home_win_prob, 4),
            "confidence_score": round(self.confidence_score, 1),
            "base_confidence": round(self.base_confidence, 1),
            "signal_delta": round(self.signal_delta, 1),
            "classification": self.classification,
            "model_prob": round(self.model_prob, 4) if self.model_prob is not None else None,
            "market_prob": round(self.market_prob, 4) if self.market_prob is not None else None,
        }


def ensemble_home_prob(model_home_prob: float | None, market_home_prob: float | None) -> float:
    """Blend model + market home win prob in logit space."""
    if model_home_prob is None and market_home_prob is None:
        return 0.5
    if model_home_prob is None:
        return clamp(market_home_prob, 0.01, 0.99)  # type: ignore[arg-type]
    if market_home_prob is None:
        return clamp(model_home_prob, 0.01, 0.99)
    blended = _W_MODEL * logit(model_home_prob) + _W_MARKET * logit(market_home_prob)
    return inv_logit(blended)


def _base_confidence(win_prob: float) -> float:
    frac = (win_prob - _PROB_FLOOR) / (_PROB_CEIL - _PROB_FLOOR)
    return clamp(_CONF_FLOOR + frac * (_CONF_CEIL - _CONF_FLOOR), _CONF_FLOOR, _CONF_CEIL)


def _signal_delta(signals: list[Signal], predicted_side: str) -> float:
    total = 0.0
    for s in signals:
        if s.severity == "info" or s.weight == 0:
            continue
        if s.severity == "warning":
            total -= s.magnitude * s.weight
        elif s.severity == "bullish":
            if s.side == predicted_side:
                total += s.magnitude * s.weight
            elif s.side in ("home", "away"):  # supports the opponent
                total -= s.magnitude * s.weight
            # side == 'game' bullish: undefined, treat as neutral
    return clamp(total, -_MAX_SIGNAL_DELTA, _MAX_SIGNAL_DELTA)


def _classify(win_prob: float, confidence: float, signals: list[Signal]) -> str:
    keys = {s.key for s in signals}
    if "upset_pressure" in keys and win_prob < 0.62:
        return "upset_watch"
    if win_prob < 0.55 or "coin_flip" in keys:
        return "coin_flip"
    if confidence >= 80 and win_prob >= 0.70:
        return "anchor"
    if confidence >= 66:
        return "strong_lean"
    return "lean"


def score_game(
    *,
    model_home_prob: float | None,
    market_home_prob: float | None,
    signals: list[Signal],
) -> GameScore:
    """Compute the ensemble win prob, 0-100 confidence, and classification."""
    home_prob = ensemble_home_prob(model_home_prob, market_home_prob)
    predicted_side = "home" if home_prob >= 0.5 else "away"
    win_prob = home_prob if predicted_side == "home" else (1.0 - home_prob)

    base = _base_confidence(win_prob)
    delta = _signal_delta(signals, predicted_side)
    confidence = clamp(base + delta, 0.0, 100.0)

    classification = _classify(win_prob, confidence, signals)

    model_pred = None
    if model_home_prob is not None:
        model_pred = model_home_prob if predicted_side == "home" else 1.0 - model_home_prob
    market_pred = None
    if market_home_prob is not None:
        market_pred = market_home_prob if predicted_side == "home" else 1.0 - market_home_prob

    return GameScore(
        predicted_winner_side=predicted_side,
        win_prob=win_prob,
        home_win_prob=home_prob,
        confidence_score=confidence,
        base_confidence=base,
        signal_delta=delta,
        classification=classification,
        model_prob=model_pred,
        market_prob=market_pred,
    )


def build_explanation(
    *,
    winner_id: str,
    loser_id: str,
    score: GameScore,
    signals: list[Signal],
) -> str:
    """Plain-English summary template (SOW 1 deliverable #10)."""
    conf = score.confidence_score
    tier = {
        "anchor": "a high-conviction anchor",
        "strong_lean": "a strong lean",
        "lean": "a modest lean",
        "coin_flip": "essentially a coin flip",
        "upset_watch": "an upset-watch spot",
    }.get(score.classification, "a lean")

    lead = (
        f"Sparky makes {winner_id} the pick over {loser_id} at "
        f"{score.win_prob * 100:.0f}% to win ({conf:.0f}/100 confidence) — {tier}."
    )
    drivers = [s for s in signals if s.severity in ("bullish", "warning") and s.weight > 0][:3]
    if drivers:
        bits = "; ".join(s.explanation for s in drivers)
        return f"{lead} Key reads: {bits}"
    if score.model_prob is not None and score.market_prob is not None:
        return (
            f"{lead} Model ({score.model_prob * 100:.0f}%) and market "
            f"({score.market_prob * 100:.0f}%) are broadly aligned with no standout market signals."
        )
    return lead
