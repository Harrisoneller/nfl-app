"""Structured access logging.

Emits one log line per request with method, path, status, duration_ms,
and the request_id from RequestIDMiddleware. Health/readiness pings are
logged at debug to avoid spam.
"""
from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from ..logging_config import get_logger

log = get_logger("access")

QUIET_PATHS = ("/health", "/live", "/ready")


class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
            log.exception(
                "request_error",
                method=request.method,
                path=request.url.path,
                duration_ms=elapsed_ms,
            )
            raise
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        method = request.method
        path = request.url.path
        if path in QUIET_PATHS:
            log.debug("request", method=method, path=path, status=status, duration_ms=elapsed_ms)
        else:
            log.info("request", method=method, path=path, status=status, duration_ms=elapsed_ms)
        return response
