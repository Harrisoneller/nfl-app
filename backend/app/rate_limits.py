"""slowapi rate limiter, single shared instance.

Limits are per-IP by default. When MULTI_USER_MODE=true, you'll want to
key on the JWT sub claim instead — the limiter accepts a custom key_func
per route if needed.
"""
from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from .config import get_settings

settings = get_settings()
limiter = Limiter(key_func=get_remote_address, default_limits=[settings.rate_limit_default])
