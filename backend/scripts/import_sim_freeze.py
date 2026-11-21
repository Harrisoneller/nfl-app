"""Publish one week's frozen drive-sim file into the read-only JSON store.

This is a batch step. It checks the file sha256, drops every column except
the sim total, the home-perspective sim margin, and the sim home win
probability, and writes those rows. It does not run the simulator and it is
not called by the API.

From backend/:

    python scripts/import_sim_freeze.py \
        --file frozen_week.json \
        --sha256 <sha256 of that file> \
        --source-commit <research commit> \
        --frozen-at 2026-11-20T18:00:00Z

Run it after the research freeze commit and before that week's first kickoff.
A mismatch or a conflict with a game already stored leaves the store as it was.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.sim_panel_store import (
    FrozenSimLocked,
    HashMismatch,
    MarketFieldRejected,
    SimFreezeError,
    import_week,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import one frozen sim week.")
    parser.add_argument("--file", required=True, help="Frozen week JSON or CSV")
    parser.add_argument("--sha256", required=True, help="Expected sha256 of --file")
    parser.add_argument("--source-commit", help="Research commit that froze the file")
    parser.add_argument("--frozen-at", help="Timezone-aware ISO timestamp of the freeze")
    parser.add_argument("--store", help="Override the JSON store path")
    args = parser.parse_args(argv)
    try:
        result = import_week(
            args.file,
            args.sha256,
            source_commit=args.source_commit,
            frozen_at=args.frozen_at,
            store_path=args.store,
        )
    except HashMismatch as exc:
        print(f"rejected: {exc}", file=sys.stderr)
        return 2
    except (MarketFieldRejected, FrozenSimLocked, SimFreezeError) as exc:
        print(f"rejected: {exc}", file=sys.stderr)
        return 1
    action = "wrote" if result.written else "unchanged"
    print(
        f"{action} {result.games} games for {result.season} week {result.week} "
        f"in {result.store_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
