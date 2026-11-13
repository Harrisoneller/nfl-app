"""Opt-in repair for nflverse kickoffs stored before the Eastern-to-UTC fix.

The old parser saved the Eastern wall clock with a UTC label. This script
re-reads those digits as America/New_York, writes the real UTC instant, and
stamps ``raw.kickoff_zone`` so a second run does not shift the row again.

Rows written by the fixed ingest already carry that stamp and are skipped.
The next schedule ingest also overwrites ``start_time`` and sets the stamp,
so this script is only for rows that have not been refreshed yet.

Dry-run unless ``--apply`` is passed. It is not an Alembic migration.
``alembic upgrade head`` (the web start command) does not run it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow `python scripts/repair_nflverse_kickoffs.py` from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal
from app.models.game import Game
from app.services.scores_service import apply_kickoff_repair, kickoff_repair_needed


def repair_games(apply: bool) -> dict[str, int]:
    db = SessionLocal()
    seen = 0
    changed = 0
    try:
        for game in db.query(Game).all():
            if not kickoff_repair_needed(game):
                continue
            seen += 1
            if apply_kickoff_repair(game):
                changed += 1
            if not apply:
                db.expunge(game)
        if apply and seen:
            db.commit()
        else:
            db.rollback()
    finally:
        db.close()
    return {"candidates": seen, "changed": changed, "applied": int(apply)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the repair. Without this flag the script only counts rows.",
    )
    args = parser.parse_args()
    result = repair_games(args.apply)
    print(result)


if __name__ == "__main__":
    main()
