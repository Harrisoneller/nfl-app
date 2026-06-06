"""Sparky intelligence layer (SOW 1).

A self-contained, dependency-light quant engine for NFL betting intelligence.
Everything in this package is pure Python and side-effect free so it can be unit
tested without a database or network — the orchestration that reads/writes the
DB and calls the existing Elo/ML predictor lives in ``app.services.sparky_service``.

Modules:
  - ``odds_math``  : american/decimal/implied conversions, de-vig, parlay odds
  - ``signals``    : the market-signal taxonomy + detection framework
  - ``confidence`` : ensemble (model + market + signals) -> 0-100 confidence
  - ``parlay``     : 3-leg parlay generation (8 combos) + composite ranking
  - ``accuracy``   : historical-accuracy formulas (rolling windows, hit rates)
"""
from __future__ import annotations

from . import accuracy, confidence, odds_math, parlay, signals  # noqa: F401
