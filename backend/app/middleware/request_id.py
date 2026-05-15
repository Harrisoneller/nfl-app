"""Request ID middleware.

Every request gets a short ULID-like ID. Bound to structlog context so
every log line during that request includes it. Also echoed back as the
`X-Request-ID` response header so the frontend / clients can correlate
failures.
"""
from __future__ import annotations

import secrets

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        rid = request.headers.get("x-request-id") or _short_id()
        request.state.request_id = rid
        # Clear contextvars on each request so logs don't bleed between requests.
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=rid, path=request.url.path)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
        response.headers["X-Request-ID"] = rid
        return response


def _short_id() -> str:
    return secrets.token_hex(8)
