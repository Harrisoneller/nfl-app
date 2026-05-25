from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from ..services import endpoint_slo_service


class EndpointSLOMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        response: Response | None = None
        try:
            response = await call_next(request)
            return response
        finally:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            status_code = response.status_code if response is not None else 500
            cache_status = getattr(request.state, "cache_status", None)
            if cache_status is None and response is not None:
                cache_status = response.headers.get("X-Cache-Status")
            endpoint_slo_service.track_request(
                request,
                duration_ms=elapsed_ms,
                status_code=status_code,
                cache_status=cache_status,
            )
