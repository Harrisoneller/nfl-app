"""Sentry initialization — no-op if SENTRY_DSN is empty."""
from __future__ import annotations

from .config import get_settings
from .logging_config import get_logger

log = get_logger(__name__)


def init_sentry() -> bool:
    """Returns True if Sentry was initialized."""
    s = get_settings()
    if not s.sentry_dsn:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError:
        log.warning("sentry_sdk_not_installed")
        return False
    sentry_sdk.init(
        dsn=s.sentry_dsn,
        environment=s.app_env,
        traces_sample_rate=s.sentry_traces_sample_rate,
        integrations=[
            FastApiIntegration(),
            StarletteIntegration(),
            SqlalchemyIntegration(),
        ],
    )
    log.info("sentry_initialized")
    return True
