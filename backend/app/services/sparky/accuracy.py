"""Historical-accuracy formulas (SOW 1 §8 / SOW 2 §9).

Pure functions over already-fetched, plain-dict rows so they're trivially
testable. The service is responsible for querying the DB and shaping rows.

Individual-pick row schema:
    {"date": date|"YYYY-MM-DD", "correct": bool|None,
     "confidence": float|None, "classification": str|None,
     "signals": list[str]}

Parlay-result row schema:
    {"date": date|"YYYY-MM-DD", "rank_1_hit": bool|None,
     "top_3": bool|None, "top_4": bool|None, "winning_rank": int|None}
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Iterable

WINDOWS = {"daily": 1, "rolling_7d": 7, "rolling_21d": 21, "rolling_30d": 30}
CONFIDENCE_BANDS = [(45, 55), (55, 65), (65, 75), (75, 85), (85, 100)]


def _as_date(v: Any) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str) and v:
        try:
            return datetime.fromisoformat(v[:10]).date()
        except ValueError:
            return None
    return None


def _pct(n: int, d: int) -> float | None:
    return round(100.0 * n / d, 1) if d else None


# --------------------------------------------------------------------------- #
# Individual picks
# --------------------------------------------------------------------------- #


def pick_accuracy(rows: Iterable[dict]) -> dict:
    """Correct / total over the given rows (settled picks only)."""
    settled = [r for r in rows if r.get("correct") is not None]
    correct = sum(1 for r in settled if r["correct"])
    return {"n": len(settled), "correct": correct, "accuracy_pct": _pct(correct, len(settled))}


def _window_rows(rows: list[dict], as_of: date, days: int) -> list[dict]:
    start = as_of - timedelta(days=days - 1)
    out = []
    for r in rows:
        d = _as_date(r.get("date"))
        if d is not None and start <= d <= as_of:
            out.append(r)
    return out


def rolling_pick_accuracy(rows: list[dict], as_of: date | None = None) -> dict:
    """Daily + rolling 7/21/30-day individual-pick accuracy."""
    settled = [r for r in rows if r.get("correct") is not None]
    if as_of is None:
        dates = [d for r in settled if (d := _as_date(r.get("date")))]
        as_of = max(dates) if dates else date.today()
    return {
        name: pick_accuracy(_window_rows(settled, as_of, days))
        for name, days in WINDOWS.items()
    }


def accuracy_by_confidence_band(rows: Iterable[dict]) -> list[dict]:
    """Calibration check: accuracy bucketed by predicted confidence."""
    settled = [r for r in rows if r.get("correct") is not None and r.get("confidence") is not None]
    out = []
    for lo, hi in CONFIDENCE_BANDS:
        band = [r for r in settled if lo <= r["confidence"] < hi or (hi == 100 and r["confidence"] == 100)]
        correct = sum(1 for r in band if r["correct"])
        out.append({
            "band": f"{lo}-{hi}",
            "n": len(band),
            "correct": correct,
            "accuracy_pct": _pct(correct, len(band)),
        })
    return out


def accuracy_by_signal(rows: Iterable[dict]) -> list[dict]:
    """Per-signal-type accuracy — which signals actually predict winners."""
    buckets: dict[str, list[bool]] = defaultdict(list)
    for r in rows:
        if r.get("correct") is None:
            continue
        for key in r.get("signals") or []:
            buckets[key].append(bool(r["correct"]))
    out = []
    for key, results in buckets.items():
        correct = sum(1 for x in results if x)
        out.append({
            "signal": key,
            "n": len(results),
            "correct": correct,
            "accuracy_pct": _pct(correct, len(results)),
        })
    out.sort(key=lambda x: (x["accuracy_pct"] is not None, x["accuracy_pct"] or 0, x["n"]), reverse=True)
    return out


# --------------------------------------------------------------------------- #
# Parlays
# --------------------------------------------------------------------------- #


def parlay_rates(rows: Iterable[dict]) -> dict:
    """Rank-#1 hit rate + Top-3 / Top-4 containment over the given parlay rows."""
    settled = [r for r in rows if r.get("rank_1_hit") is not None]
    n = len(settled)
    r1 = sum(1 for r in settled if r.get("rank_1_hit"))
    t3 = sum(1 for r in settled if r.get("top_3"))
    t4 = sum(1 for r in settled if r.get("top_4"))
    return {
        "n": n,
        "rank_1_hit_rate": _pct(r1, n),
        "top_3_containment": _pct(t3, n),
        "top_4_containment": _pct(t4, n),
    }


def rolling_parlay_rates(rows: list[dict], as_of: date | None = None) -> dict:
    settled = [r for r in rows if r.get("rank_1_hit") is not None]
    if as_of is None:
        dates = [d for r in settled if (d := _as_date(r.get("date")))]
        as_of = max(dates) if dates else date.today()
    return {
        name: parlay_rates(_window_rows(settled, as_of, days))
        for name, days in WINDOWS.items()
    }


# --------------------------------------------------------------------------- #
# Roll-up
# --------------------------------------------------------------------------- #


def performance_trends(pick_rows: list[dict], parlay_rows: list[dict]) -> dict:
    """Best/worst signal + overall summary for the dashboard header."""
    by_sig = [s for s in accuracy_by_signal(pick_rows) if s["n"] >= 3 and s["accuracy_pct"] is not None]
    best = by_sig[0] if by_sig else None
    worst = by_sig[-1] if by_sig else None
    overall_pick = pick_accuracy(pick_rows)
    overall_parlay = parlay_rates(parlay_rows)
    return {
        "overall_pick_accuracy_pct": overall_pick["accuracy_pct"],
        "overall_parlay_rank1_pct": overall_parlay["rank_1_hit_rate"],
        "overall_parlay_top3_pct": overall_parlay["top_3_containment"],
        "best_signal": best,
        "worst_signal": worst,
        "n_picks_settled": overall_pick["n"],
        "n_parlays_settled": overall_parlay["n"],
    }
