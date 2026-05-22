"""Startup self-check.

Runs once at boot and logs clear warnings for the most common
"the app starts but X is broken" failure modes. Doesn't raise — we'd
rather start in a degraded state and have the warnings visible than
fail to boot.
"""
from __future__ import annotations

from sqlalchemy import inspect, text

from .config import get_settings
from .db import SessionLocal, engine
from .logging_config import get_logger

log = get_logger(__name__)

# Tables we expect to exist after `alembic upgrade head` ran successfully.
EXPECTED_TABLES = [
    "users", "teams", "players", "games", "game_stats",
    "news_items", "odds_lines",
    "widgets", "chat_sessions", "chat_messages",
    "team_elo_ratings",
    "model_artifacts",
    "data_sync_runs",
    "player_season_stats",
    "team_season_aggregates",
    "team_metric_values",
    "player_metric_values",
]


def run() -> None:
    """Logs a tagged summary at INFO if all-clear, WARNING when degraded."""
    s = get_settings()
    issues: list[str] = []

    # ---- Database connectivity ---------------------------------------------
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001
        log.error("doctor_db_unreachable", error=str(e)[:200], url=_safe_url(s.database_url))
        issues.append("database unreachable")
        log.warning("doctor_summary", issues=issues, status="degraded")
        return  # nothing else we can check without a DB

    # ---- Tables present ----------------------------------------------------
    try:
        insp = inspect(engine)
        existing = set(insp.get_table_names())
    except Exception as e:  # noqa: BLE001
        log.warning("doctor_inspect_failed", error=str(e)[:200])
        existing = set()
    missing = [t for t in EXPECTED_TABLES if t not in existing]
    if missing:
        log.warning(
            "doctor_missing_tables", missing=missing,
            hint="Run: alembic upgrade head",
        )
        issues.append(f"missing tables: {','.join(missing)}")

    # ---- Required env / secrets --------------------------------------------
    if not s.grok_api_key and s.llm_provider == "grok":
        log.warning("doctor_missing_grok_api_key",
                    hint="Set GROK_API_KEY in .env to enable AI features")
        issues.append("GROK_API_KEY not set")
    if s.secret_key == "change-me":
        log.warning("doctor_default_secret_key",
                    hint="Generate one: python -c 'import secrets; print(secrets.token_urlsafe(64))'")
        issues.append("SECRET_KEY is default placeholder")

    if s.app_env == "production" and s.app_role == "web":
        log.info(
            "doctor_web_role",
            hint="Scheduler is off on APP_ROLE=web. Run a separate worker service with "
            "APP_ROLE=worker and the same DATABASE_URL, or data will not refresh.",
        )
    if s.app_env == "production" and s.app_role == "worker":
        log.info("doctor_worker_role", boot_warmup=s.boot_warmup_level)

    # ---- Teams seeded? -----------------------------------------------------
    try:
        db = SessionLocal()
        try:
            n_teams = db.execute(text("SELECT count(*) FROM teams")).scalar() or 0
            if n_teams < 32:
                log.info("doctor_teams_not_seeded", count=n_teams,
                         hint="Scheduler will seed them within a few seconds of boot")
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        pass

    if issues:
        log.warning("doctor_summary", issues=issues, status="degraded")
    else:
        log.info("doctor_summary", status="all clear")


def _safe_url(url: str) -> str:
    """Strip password from a DB URL for safe logging."""
    if "@" not in url:
        return url
    head, tail = url.rsplit("@", 1)
    if "//" in head and ":" in head.split("//", 1)[1]:
        scheme, rest = head.split("//", 1)
        user, _ = rest.split(":", 1)
        return f"{scheme}//{user}:***@{tail}"
    return url
