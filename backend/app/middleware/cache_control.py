"""Cache-Control middleware.

Adds appropriate Cache-Control headers based on URL pattern so a CDN (or
the browser) can short-circuit identical requests. Conservative defaults:

- /meta/seasons, /teams (no slug)        → 1h public
- /teams/{id}, /players/{id} (profile)   → 5 min public
- /predictions/standings/projected       → 30 min public
- /predictions/elo/current               → 10 min public
- /betting/teams/{id}/history            → 1h public
- Everything else                        → no-store (default)
"""
from __future__ import annotations

import re

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Regex patterns matched against the request path, mapped to (max_age_seconds, public_flag).
_RULES = [
    (re.compile(r"^/meta/seasons$"),                                   (3600, True)),
    (re.compile(r"^/teams$"),                                          (3600, True)),
    (re.compile(r"^/teams/[A-Z]+$"),                                   (3600, True)),
    (re.compile(r"^/teams/[A-Z]+/(roster|schedule)$"),                 (600, True)),
    (re.compile(r"^/teams/[A-Z]+/(profile|trend)$"),                   (300, True)),
    (re.compile(r"^/players/[^/]+$"),                                  (3600, True)),
    (re.compile(r"^/players/[^/]+/(profile|gamelog|trend|news)$"),     (300, True)),
    (re.compile(r"^/predictions/standings/projected$"),                (1800, True)),
    (re.compile(r"^/predictions/elo/current$"),                        (600, True)),
    (re.compile(r"^/predictions/teams/[A-Z]+/(season|elo-history)$"),  (600, True)),
    (re.compile(r"^/predictions/teams/[A-Z]+/remaining-schedule$"),    (600, True)),
    (re.compile(r"^/predictions/awards$"),                             (1800, True)),
    (re.compile(r"^/betting/teams/[A-Z]+/history$"),                   (3600, True)),
    (re.compile(r"^/betting/edge$"),                                   (300, True)),
    (re.compile(r"^/betting/best-bets$"),                              (300, True)),
    (re.compile(r"^/news$"),                                           (60, True)),
    (re.compile(r"^/odds$"),                                           (60, True)),
    (re.compile(r"^/scores$"),                                         (15, True)),
]


class CacheControlMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        # Never cache POST/PUT/DELETE responses
        if request.method != "GET":
            return response
        # Never cache error responses
        if response.status_code >= 400:
            return response
        # Skip if a handler already set Cache-Control
        if "cache-control" in {k.lower() for k in response.headers.keys()}:
            return response

        path = request.url.path
        for pattern, (max_age, public) in _RULES:
            if pattern.match(path):
                visibility = "public" if public else "private"
                response.headers["Cache-Control"] = (
                    f"{visibility}, max-age={max_age}, "
                    f"stale-while-revalidate={max_age // 2}"
                )
                break
        return response
