#!/usr/bin/env python
"""
Sparky Backtesting CLI

Usage examples:

    # Replay mode using historical snapshots (best for validation)
    python -m scripts.run_sparky_backtest \
        --start 2024-09-01 --end 2025-01-20 \
        --mode replay --hours-cutoff 48

    # Analyze already settled results (fast, doesn't need raw snapshots)
    python -m scripts.run_sparky_backtest \
        --start 2024-09-01 --end 2025-01-20 \
        --mode settled

    # Save full JSON report
    python -m scripts.run_sparky_backtest --start ... --end ... --json /tmp/sparky-backtest.json

Prerequisites
-------------
- Run `alembic upgrade head` first (the `odds_snapshots` table is required for replay mode).
- For best results with --mode replay, you need historical data in `odds_snapshots`.
  If the table is empty or missing, use --mode settled or seed demo data first.

Requires the app's database to be accessible (same as running the API).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

# Make the app importable when running as a script
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.services.sparky.backtest import BacktestConfig, BacktestResult, run_backtest


def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def print_nice_report(result: BacktestResult) -> None:
    print("\n" + "=" * 70)
    print(result.summary())
    print("=" * 70)

    print("\n--- Pick Level ---")
    pa = result.pick_accuracy
    print(f"  Accuracy: {pa.get('accuracy_pct')}% on {pa.get('n')} settled games")

    if result.brier_score is not None:
        print(f"  Brier Score: {result.brier_score:.4f} (lower is better)")
    if result.log_loss is not None:
        print(f"  Log Loss:   {result.log_loss:.4f} (lower is better)")

    print("\n--- Calibration (predicted vs actual) ---")
    for row in result.calibration:
        print(f"  {row['band']:>12} | n={row['n']:>3} | pred={row.get('avg_predicted') or '—':>5} | actual={row.get('accuracy') or '—':>5}")

    if result.signal_performance:
        print("\n--- Signal Performance (lift when signal present) ---")
        for s in result.signal_performance[:8]:  # top 8
            lift = s.get("lift")
            lift_str = f"{lift:+.1f}pp" if lift is not None else "—"
            print(f"  {s['signal']:25} | with={s['acc_with'] or '—':>5}% (n={s['n_with']}) | lift={lift_str}")

    print("\n--- Parlay Performance ---")
    pm = result.parlay_metrics
    if pm.get("n_parlays", 0) > 0:
        print(f"  Rank #1 hit rate: {pm.get('rank1_hit_rate')}%")
        print(f"  Top-3 containment: {pm.get('top3_containment')}%")
        print(f"  Total parlays evaluated: {pm['n_parlays']}")
    else:
        slates = pm.get("slates_processed", 0)
        print(f"  (not enough data for parlay analysis)")
        print(f"   → Processed {slates} slates, but none had 3+ games with predictions.")
        print(f"   → This is common with demo data or narrow date ranges.")
        print(f"   → Try a wider --start/--end range or seed more demo data.")

    roi = result.roi_simulation
    if roi.get("n_bets"):
        print("\n--- Simulated Betting Performance ---")
        print(f"  Positive edge bets: {roi['n_bets']}")
        print(f"  Flat 2% ROI: {roi.get('flat_roi_pct', 0):+0.1f}%")
        print(f"  0.25 Kelly ROI: {roi.get('kelly_roi_pct', 0):+0.1f}%")

    print("\n" + "=" * 70)
    print("Backtest complete. Use --json <path> to export full structured data.")
    print("=" * 70 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Sparky historical backtest")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--mode", choices=["replay", "settled"], default="replay",
                        help="replay = re-run current engine on snapshots; settled = use existing results")
    parser.add_argument("--hours-cutoff", type=float, default=None,
                        help="Only use snapshots this many hours before kickoff (replay mode)")
    parser.add_argument("--min-snapshots", type=int, default=2)
    parser.add_argument("--json", type=str, default=None, help="Write full result as JSON to this path")
    parser.add_argument("--quiet", action="store_true", help="Only output JSON (if --json) or minimal text")

    args = parser.parse_args()

    cfg = BacktestConfig(
        start_date=parse_date(args.start),
        end_date=parse_date(args.end),
        mode=args.mode,
        min_snapshots_per_game=args.min_snapshots,
        hours_before_kickoff_cutoff=args.hours_cutoff,
    )

    db: Session = SessionLocal()
    try:
        result: BacktestResult = run_backtest(db, cfg)

        if args.json:
            payload = {
                "config": {
                    "start": str(cfg.start_date),
                    "end": str(cfg.end_date),
                    "mode": cfg.mode,
                    "hours_cutoff": cfg.hours_before_kickoff_cutoff,
                },
                "summary": result.summary(),
                "metrics": {
                    "pick_accuracy": result.pick_accuracy,
                    "brier_score": result.brier_score,
                    "log_loss": result.log_loss,
                    "calibration": result.calibration,
                    "signal_performance": result.signal_performance,
                    "parlay": result.parlay_metrics,
                    "roi": result.roi_simulation,
                },
                "n_games": result.n_games,
                "n_slates": result.n_slates,
                "generated_at": result.generated_at.isoformat(),
            }
            Path(args.json).write_text(json.dumps(payload, indent=2, default=str))
            if not args.quiet:
                print(f"Wrote full report to {args.json}")

        if not args.quiet:
            print_nice_report(result)
        elif not args.json:
            print(result.summary())

    except RuntimeError as e:
        # Friendly message for common setup issues (missing tables, etc.)
        print("\n" + "=" * 70)
        print("BACKTEST FAILED")
        print("=" * 70)
        print(str(e))
        print("\nCommon fixes:")
        print("  1. Run database migrations:")
        print("       alembic upgrade head")
        print("  2. If you only have prediction results (no raw line history), try:")
        print("       python -m scripts.run_sparky_backtest --start ... --end ... --mode settled")
        print("=" * 70 + "\n")
        sys.exit(1)

    except Exception as e:
        print(f"\nUnexpected error: {e}")
        print("Check the full traceback above for details.")
        raise

    finally:
        db.close()


if __name__ == "__main__":
    main()
