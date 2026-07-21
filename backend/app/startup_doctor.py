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
    "feature_snapshots",
    "model_lifecycle_runs",
    "endpoint_slo_snapshots",
    "experiment_events",
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
    secret_is_placeholder = (
        not s.secret_key
        or s.secret_key == "change-me"
        or s.secret_key.startswith("change-me")
    )
    if secret_is_placeholder:
        # Always bad; *critical* once auth is load-bearing, because a rotating or
        # guessable SECRET_KEY silently invalidates (or forges) JWTs.
        if s.multi_user_mode:
            log.error(
                "doctor_default_secret_key_multi_user",
                hint="MULTI_USER_MODE=true but SECRET_KEY is a placeholder. Real logins "
                "depend on it — set a stable random value on every service: "
                "python -c 'import secrets; print(secrets.token_urlsafe(64))'",
            )
            issues.append("SECRET_KEY is placeholder while MULTI_USER_MODE=true (logins unreliable)")
        else:
            log.warning("doctor_default_secret_key",
                        hint="Generate one: python -c 'import secrets; print(secrets.token_urlsafe(64))'")
            issues.append("SECRET_KEY is default placeholder")

    # ---- Auth posture: the ADMIN_EMAILS + single-user trap -----------------
    # When ADMIN_EMAILS is set but MULTI_USER_MODE is off, get_current_user
    # ignores the login JWT and resolves EVERY request to the seeded
    # system@local user — whose email is not in the allowlist — so require_admin
    # 403s and /auth/me reports is_admin=false. Net effect: a successful admin
    # login still can't reach /admin, and the account shows as system@local.
    if s.admin_email_set and not s.multi_user_mode:
        log.warning(
            "doctor_admin_emails_without_multi_user",
            admin_emails=sorted(s.admin_email_set),
            hint="ADMIN_EMAILS is set but MULTI_USER_MODE=false, so all requests resolve "
            "to system@local and NO ONE can reach admin routes (the login JWT is ignored). "
            "Set MULTI_USER_MODE=true to honor logins and unlock admin for the allowlisted "
            "email(s).",
        )
        issues.append("ADMIN_EMAILS set but MULTI_USER_MODE=false (admin unreachable)")

    if s.app_env == "production" and s.app_role == "web":
        log.info(
            "doctor_web_role",
            hint="Scheduler is off on APP_ROLE=web. Run a separate worker service with "
            "APP_ROLE=worker and the same DATABASE_URL, or data will not refresh.",
        )
        if s.cors_allows_only_localhost and not s.cors_allow_vercel_regex:
            log.warning(
                "doctor_cors_localhost_only",
                cors_origins=s.cors_origins,
                hint="Set CORS_ORIGINS to your Vercel URL on the web service, then redeploy. "
                "Browser CORS preflight will fail with 400 until the Origin matches.",
            )
            issues.append("CORS_ORIGINS not set for production frontend")
        elif s.cors_allows_only_localhost and s.cors_allow_vercel_regex:
            log.info(
                "doctor_cors_vercel_regex_fallback",
                hint="CORS_ORIGINS is localhost-only but *.vercel.app is allowed via regex. "
                "Add your canonical Vercel URL to CORS_ORIGINS for custom domains.",
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
