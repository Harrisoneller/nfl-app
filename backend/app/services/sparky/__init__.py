"""Sparky intelligence layer (SOW 1).

A self-contained, dependency-light quant engine for NFL betting intelligence.
Everything in this package is pure Python and side-effect free so it can be unit
tested without a database or network — the orchestration that reads/writes the
DB and calls the existing Elo/ML predictor lives in ``app.services.sparky_service``.

Modules:
  - ``odds_math``    : american/decimal/implied conversions, de-vig (power and
                       proportional), parlay odds, moment-based Kelly
  - ``signals``      : the market-signal taxonomy + detection framework
  - ``confidence``   : ensemble (model + market + signals) -> 0-100 confidence
  - ``shrinkage``    : calibration, edge shrinkage toward the market, and the
                       winner's-curse correction — the three things that decide
                       whether a claimed edge is real
  - ``correlation``  : push-aware, correlation-aware parlay pricing over a
                       latent-factor model of *estimation* error
  - ``legs``         : the candidate leg universe (moneyline / spread / total)
  - ``parlay``       : slate-wide search, +EV gate, growth-rate ranking
  - ``value``        : per-leg bet quality — EV at the offered price, how likely
                       that EV is real rather than estimation noise, expected
                       log-growth, fractional-Kelly stake, and the tiering the
                       Value Board renders
  - ``accuracy``     : historical-accuracy formulas (rolling windows, hit rates)
"""
from __future__ import annotations

from . import (  # noqa: F401
    accuracy,
    confidence,
    correlation,
    legs,
    odds_math,
    parlay,
    shrinkage,
    signals,
    value,
)
