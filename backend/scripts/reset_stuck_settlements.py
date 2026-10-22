#!/usr/bin/env python
"""Reset games left in ``settlement_status='settling'``.

Settlement claims a game, commits that claim, then grades inside a second
transaction. If the grading transaction rolls back (or the process dies), the
claim stays ``settling`` so the scores job will not grade or insert again.
This script finds those rows and clears the claim so a later settlement can
retry. It does not change scores, bets, or Sparky results, and it does not
VACUUM.

Dry-run is the default. Nothing is written unless ``--apply`` is passed.
The script refuses to run when ``APP_ENV`` is production or the database URL
looks like a hosted production database. Do not point it at production.

Usage (from ``backend/``)::

    python -m scripts.reset_stuck_settlements
    python -m scripts.reset_stuck_settlements --apply
"""
from __future__ import annotations

import argparse
import sys

_REMOTE_MARKERS = (
    "railway",
    "rlwy.net",
    "neon.tech",
    "amazonaws.com",
    "render.com",
    "supabase.co",
    "heroku.com",
)


def guard_not_production(app_env: str, database_url: str) -> None:
    """Raise ``SystemExit`` before a connection when the target looks production.

    The message does not include the URL (it may carry a password).
    """
    env = (app_env or "").strip().lower()
    url = (database_url or "").strip().lower()
    if env == "production" or any(marker in url for marker in _REMOTE_MARKERS):
        raise SystemExit(
            "refusing to reset stuck settlements: target looks like production. "
            "This script does not run against production."
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Find games stuck mid-settlement and reset the claim. "
            "Dry-run unless --apply. Refuses production. Does not VACUUM."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the resets. Omit to print the rows and change nothing.",
    )
    return parser.parse_args(argv)


def run(db, *, apply: bool) -> dict:
    """Find stuck games. Update them only when ``apply`` is true."""
    from app.services.settlement_service import find_stuck_game_ids, reset_stuck_games

    stuck = find_stuck_game_ids(db)
    if not apply:
        return {"dry_run": True, "stuck_game_ids": stuck, "reset": 0}
    reset = reset_stuck_games(db, stuck)
    db.commit()
    return {"dry_run": False, "stuck_game_ids": stuck, "reset": reset}


def format_report(report: dict) -> str:
    mode = "dry-run" if report["dry_run"] else "apply"
    lines = [
        f"{mode}: {len(report['stuck_game_ids'])} game(s) stuck mid-settlement"
    ]
    for game_id in report["stuck_game_ids"]:
        lines.append(f"  {game_id}")
    if report["dry_run"]:
        lines.append("no rows changed; re-run with --apply to reset them")
    else:
        lines.append(f"reset {report['reset']} game(s)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # Guard before importing the engine so a production URL is never contacted
    # just to decide we should refuse.
    from app.config import get_settings

    settings = get_settings()
    guard_not_production(settings.app_env, settings.database_url)

    from app.db import SessionLocal

    db = SessionLocal()
    try:
        report = run(db, apply=args.apply)
    finally:
        db.close()
    print(format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
