"""Startup self-check.

``run`` logs warnings for degraded boots and does not raise — a missing
table should not take the API down in development.

``enforce_production_auth`` is the exception. When the process is in
production it refuses to start unless multi-user auth has an admin
allowlist and ``SECRET_KEY`` is a long, non-placeholder random value.
Single-user mode is never a passing production posture. Development
and tests skip the check.
"""
from __future__ import annotations

import math
import os
from collections import Counter

from sqlalchemy import inspect, text

from .config import Settings, get_settings
from .db import SessionLocal, engine
from .logging_config import get_logger

log = get_logger(__name__)

# ``.env.example`` uses ``change-me-to-a-long-random-string``. Any value
# that starts with this prefix is the placeholder, including the bare
# ``change-me``.
_SECRET_PLACEHOLDER_PREFIX = "change-me"
# Production secrets must be long and not a repeated or tiny alphabet.
# ``secrets.token_urlsafe(64)`` clears both bars by a wide margin.
_MIN_SECRET_CHARS = 32
_MIN_UNIQUE_CHARS = 10
_MIN_SHANNON_BITS = 3.0

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


class ProductionConfigError(RuntimeError):
    """Production startup refused because auth would fail open."""


def is_production_runtime(settings: Settings | None = None) -> bool:
    """True when this process should fail closed.

    Production is any of:

    * ``APP_ENV=production`` (``settings.app_env``).
    * ``RAILWAY_ENVIRONMENT`` or ``RAILWAY_ENVIRONMENT_NAME`` set to any
      non-empty value. That includes ``APP_ENV=development`` on Railway, so
      labeling the process development does not skip the boot check.

    Dev and pytest set ``APP_ENV=development`` and leave both Railway
    variables unset, so they stay permissive.
    """
    s = settings if settings is not None else get_settings()
    if s.app_env == "production":
        return True
    railway_values = (
        os.environ.get("RAILWAY_ENVIRONMENT", ""),
        os.environ.get("RAILWAY_ENVIRONMENT_NAME", ""),
    )
    return any(value.strip() for value in railway_values)


def _secret_is_placeholder(secret: str | None) -> bool:
    value = (secret or "").strip()
    if not value:
        return True
    return value.lower().startswith(_SECRET_PLACEHOLDER_PREFIX)


def _shannon_bits(value: str) -> float:
    counts = Counter(value)
    n = len(value)
    return -sum((count / n) * math.log2(count / n) for count in counts.values())


def _secret_boot_problem(secret: str | None) -> str | None:
    """Why this secret cannot boot production. Never includes the value."""
    value = (secret or "").strip()
    if _secret_is_placeholder(value):
        return (
            "SECRET_KEY is missing or equals 'change-me' "
            "(any value starting with 'change-me' is the placeholder). "
            "Set a random string of at least 32 characters, the same value "
            "on every service: "
            "python -c \"import secrets; print(secrets.token_urlsafe(64))\""
        )
    low_entropy = (
        len(set(value)) < _MIN_UNIQUE_CHARS
        or _shannon_bits(value) < _MIN_SHANNON_BITS
    )
    if len(value) < _MIN_SECRET_CHARS or low_entropy:
        return (
            "SECRET_KEY must be at least 32 characters and pass a basic "
            "entropy check (at least 10 distinct characters, and not a "
            "repeated or low-entropy pattern). "
            "Set a random string, the same value on every service: "
            "python -c \"import secrets; print(secrets.token_urlsafe(64))\""
        )
    return None


def production_boot_problems(settings: Settings | None = None) -> list[str]:
    """Reasons production must not serve traffic. Empty outside production.

    The only passing posture is real multi-user auth: ``MULTI_USER_MODE=true``
    and a non-empty ``ADMIN_EMAILS`` list, plus a ``SECRET_KEY`` of at least
    32 characters that is not the ``change-me`` placeholder and is not
    low-entropy. Single-user mode makes every caller the seeded system admin
    when the allowlist is empty, and makes admin unreachable when the
    allowlist is set (the login token is ignored). Neither is allowed in
    production.

    The messages name the variables and the expected shape. They never
    include the secret value.
    """
    s = settings if settings is not None else get_settings()
    if not is_production_runtime(s):
        return []

    problems: list[str] = []
    secret_problem = _secret_boot_problem(s.secret_key)
    if secret_problem:
        problems.append(secret_problem)

    # Single-user mode is never a production admin mode. An empty allowlist
    # means every caller is system@local with is_admin. A non-empty allowlist
    # ignores bearer tokens and then 403s that same account.
    if not s.multi_user_mode and s.admin_email_set:
        problems.append(
            "Auth isn't configured: ADMIN_EMAILS is set but MULTI_USER_MODE "
            "is false, so login tokens are ignored and every caller is "
            "system@local, which is not on the allowlist. Production does not "
            "allow single-user mode. Set MULTI_USER_MODE=true and keep "
            "ADMIN_EMAILS as a comma-separated list of admin emails."
        )
    elif not s.multi_user_mode:
        problems.append(
            "Auth isn't configured: MULTI_USER_MODE is false and ADMIN_EMAILS "
            "is empty, so every caller is the seeded system admin. Production "
            "does not allow that. Set MULTI_USER_MODE=true and ADMIN_EMAILS "
            "to a comma-separated list of admin emails "
            "(for example admin@example.com)."
        )
    elif not s.admin_email_set:
        problems.append(
            "Auth isn't configured: MULTI_USER_MODE=true but no admin list "
            "is set. Set ADMIN_EMAILS to a comma-separated list of admin "
            "emails (for example admin@example.com,other@example.com)."
        )
    return problems


def enforce_production_auth(settings: Settings | None = None) -> None:
    """Refuse to start in production when auth would fail open.

    Development and tests return immediately. The raised message lists
    every problem so one boot shows the full set of missing variables.
    """
    problems = production_boot_problems(settings)
    if not problems:
        return
    for problem in problems:
        log.error("production_auth_refused", reason=problem)
    raise ProductionConfigError(
        "Refusing to start in production:\n- " + "\n- ".join(problems)
    )


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
                hint="CORS_ORIGINS is localhost-only. Preview hosts for "
                "CORS_VERCEL_PROJECT are allowed via regex. Set CORS_ORIGINS or "
                "CORS_PRODUCTION_ORIGINS to the canonical site.",
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
