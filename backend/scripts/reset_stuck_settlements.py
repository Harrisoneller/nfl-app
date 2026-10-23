#!/usr/bin/env python
"""Reset games left in ``settlement_status='settling'``.

Settlement claims a game, commits that claim, then grades inside a second
transaction. If the grading transaction rolls back (or the process dies), the
claim stays ``settling`` so the scores job will not grade or insert again.
This script finds those rows and clears the claim so a later settlement can
retry. It does not change scores, bets, or Sparky results, and it does not
VACUUM.

Dry-run is the default. Nothing is written unless ``--apply`` is passed.
The script fails closed. ``APP_ENV`` must be set in the environment to
``development`` or ``test``; an unset value is refused even though settings
would otherwise default to development. Production is refused. The database
host must be localhost, 127.0.0.1, ::1, or the ``postgres`` test container.
A local SQLite file has no host and is allowed. Every other host is refused.

Usage (from ``backend/``)::

    python -m scripts.reset_stuck_settlements
    python -m scripts.reset_stuck_settlements --apply
"""
from __future__ import annotations

import argparse
import os
import sys

from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import ArgumentError

# Exact hosts. Matching is not a substring check, so a name that merely
# contains "localhost" is refused. ``postgres`` is the test database container.
_ALLOWED_DB_HOSTS = frozenset({
    "localhost",
    "127.0.0.1",
    "::1",
    "postgres",
})
# Settings defaults APP_ENV to development when the variable is absent.
# The script does not accept that default.
_ALLOWED_APP_ENVS = frozenset({"development", "test"})


def _normalized_host(host: str | None) -> str:
    value = (host or "").strip().lower()
    if value.startswith("[") and value.endswith("]"):
        return value[1:-1]
    return value


def guard_local_database(app_env: str | None, database_url: str) -> None:
    """Raise ``SystemExit`` unless the target is local or the test database.

    ``APP_ENV`` must be the string ``development`` or ``test``. Unset, blank,
    and production are refused before the host is considered. The message
    does not include the URL (it may carry a password) or the host that
    was rejected.
    """
    env = app_env.strip().lower() if isinstance(app_env, str) else ""
    if env not in _ALLOWED_APP_ENVS:
        raise SystemExit(
            "refusing to reset stuck settlements: APP_ENV must be set to "
            "development or test. An unset or production value is refused."
        )
    try:
        url = make_url(database_url or "")
    except ArgumentError:
        raise SystemExit(
            "refusing to reset stuck settlements: database host is not on the "
            "allowlist. This script does not run against production."
        ) from None
    host = _normalized_host(url.host)
    driver = (url.drivername or "").split("+", 1)[0]
    if driver == "sqlite" and not host:
        return
    if host in _ALLOWED_DB_HOSTS:
        return
    raise SystemExit(
        "refusing to reset stuck settlements: database host is not on the "
        "allowlist. This script does not run against production."
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Find games stuck mid-settlement and reset the claim. "
            "Dry-run unless --apply. Local and test hosts only. Does not VACUUM."
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
    # The process environment, not Settings' development default. Refuse
    # before importing the engine so an unset or production env never connects.
    app_env = os.environ.get("APP_ENV")
    if (app_env or "").strip().lower() not in _ALLOWED_APP_ENVS:
        raise SystemExit(
            "refusing to reset stuck settlements: APP_ENV must be set to "
            "development or test. An unset or production value is refused."
        )
    from app.config import get_settings

    settings = get_settings()
    guard_local_database(app_env, settings.database_url)

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
