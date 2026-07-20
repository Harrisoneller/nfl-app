#!/usr/bin/env python3
"""Clear the note on every entry of a ranking board — order and tiers intact.

The board editor shows a live positional rank now, so the per-player note (which
the seed script used to fill with "RB07 · ADP 7.0") is redundant. This empties
those notes in place: it reads the current draft in rank order and re-saves it
through ``rankings_service.replace_entries`` with ``note=""``, keeping every
player's position and tier exactly as you left them. Your drag/reorder edits are
preserved — only the note text changes.

Run this AFTER saving any pending edits in /admin (it operates on the saved
draft, not the browser's unsaved state). It writes the draft only; re-publish in
/admin when you're ready for the public page to update.

Usage
-----
    python scripts/clear_board_notes.py                       # default board
    python scripts/clear_board_notes.py --name "My Board"     # by name
    python scripts/clear_board_notes.py --set-id 3            # by id
    python scripts/clear_board_notes.py --all                 # every board this season
    python scripts/clear_board_notes.py --dry-run             # show counts, write nothing
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

DEFAULT_SET_NAME = "Full PPR Top 300 (2026)"


def clear_one(rankings_service, db, set_detail: dict, actor: str, dry_run: bool) -> int:
    """Empty notes on one set. Returns how many notes were non-empty."""
    entries = set_detail["entries"]  # already ordered by rank
    had_notes = sum(1 for e in entries if (e.get("note") or "").strip())
    if dry_run:
        return had_notes
    payload = [
        {"player_id": e["player_id"], "tier": e["tier"], "note": ""}
        for e in entries
    ]
    rankings_service.replace_entries(db, set_detail["id"], payload, actor=actor)
    return had_notes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--name", default=None, help="Board name (default: the Full PPR board)")
    g.add_argument("--set-id", type=int, default=None, help="Board id")
    g.add_argument("--all", action="store_true", help="Every board (optionally scoped by --season)")
    ap.add_argument("--season", type=int, default=None, help="Scope --all to one season")
    ap.add_argument("--dry-run", action="store_true", help="Report only, write nothing")
    args = ap.parse_args()

    from app.db import SessionLocal
    from app.services import rankings_service

    actor = os.environ.get("SEED_ACTOR", "clear_board_notes.py")
    db = SessionLocal()
    try:
        # Resolve which sets to touch.
        summaries = rankings_service.list_sets(db, season=args.season)
        if args.all:
            targets = summaries
        elif args.set_id is not None:
            targets = [s for s in summaries if s["id"] == args.set_id]
        else:
            want = args.name or DEFAULT_SET_NAME
            targets = [s for s in summaries if s["name"] == want]

        if not targets:
            print("No matching board found. Available:", file=sys.stderr)
            for s in summaries:
                print(f"  #{s['id']}  {s['name']} ({s['season']})", file=sys.stderr)
            return 2

        total_cleared = 0
        for s in targets:
            detail = rankings_service.get_set_detail(db, s["id"])
            if detail is None:
                continue
            n = clear_one(rankings_service, db, detail, actor, args.dry_run)
            total_cleared += n
            verb = "would clear" if args.dry_run else "cleared"
            print(f"#{s['id']} '{s['name']}': {verb} {n} note(s) across {detail['entry_count']} players.")

        if args.dry_run:
            print(f"\n--dry-run: {total_cleared} note(s) would be cleared. No writes.")
        else:
            print(f"\nDone — {total_cleared} note(s) cleared. Re-publish in /admin to update the public page.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
