"""slowapi rate limiter, single shared instance.

Limits are per-IP by default. When MULTI_USER_MODE=true, you'll want to
key on the JWT sub claim instead — the limiter accepts a custom key_func
per route if needed.

Enforcement: ``default_limits`` only take effect once ``SlowAPIMiddleware``
is installed in ``main.py`` (or a route carries an explicit
``@limiter.limit`` decorator). The middleware is what applies the global
default to every route.

Storage: in-memory by default — correct and fast for a SINGLE replica, but
the counters are per-process, so two replicas would each allow the full
limit. When ``CACHE_BACKEND=redis`` (i.e. you've scaled to multiple web
replicas), we point the limiter at the same Redis so the limit is global.
"""
from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from .config import get_settings
from .logging_config import get_logger

log = get_logger(__name__)
settings = get_settings()

# Shared Redis store across replicas only when Redis is the configured cache
# backend; otherwise fall back to slowapi's default in-memory store.
_storage_uri: str | None = settings.redis_url if settings.cache_backend == "redis" else None

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.rate_limit_default],
    storage_uri=_storage_uri,
)

log.info(
    "rate_limiter_configured",
    default=settings.rate_limit_default,
    storage="redis" if _storage_uri else "memory",
)
