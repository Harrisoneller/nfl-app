"""Cache-Control middleware.

Adds explicit cache policy for hot read endpoints. Public GET endpoints get a
bounded freshness window plus stale-while-revalidate. Requests carrying
Authorization are intentionally treated as private/non-cacheable to avoid
serving personalized payloads from shared caches.
"""
from __future__ import annotations

import re

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Regex patterns matched against the request path, mapped to max-age seconds.
_RULES = [
    (re.compile(r"^/meta/seasons$"), 3600),
    (re.compile(r"^/teams$"), 3600),
    (re.compile(r"^/teams/[A-Z]+$"), 3600),
    (re.compile(r"^/teams/[A-Z]+/(roster|schedule)$"), 900),
    (re.compile(r"^/teams/[A-Z]+/(profile|trend|upcoming)$"), 300),
    (re.compile(r"^/teams/[A-Z]+/news$"), 60),
    (re.compile(r"^/players$"), 120),
    (re.compile(r"^/players/[^/]+$"), 3600),
    (re.compile(r"^/players/[^/]+/(profile|gamelog|trend)$"), 300),
    (re.compile(r"^/players/[^/]+/news$"), 60),
    (re.compile(r"^/predictions/games$"), 60),
    (re.compile(r"^/predictions/standings/projected$"), 900),
    (re.compile(r"^/predictions/elo/current$"), 300),
    (re.compile(r"^/predictions/teams/[A-Z]+/(season|elo-history|remaining-schedule)$"), 300),
    (re.compile(r"^/predictions/players/[^/]+/(games|season)$"), 120),
    (re.compile(r"^/predictions/awards$"), 900),
    (re.compile(r"^/betting/teams/[A-Z]+/history$"), 1800),
    (re.compile(r"^/betting/edge$"), 180),
    (re.compile(r"^/betting/teams/[A-Z]+/edge$"), 180),
    (re.compile(r"^/betting/best-bets$"), 180),
    (re.compile(r"^/news$"), 60),
    (re.compile(r"^/odds$"), 60),
    (re.compile(r"^/odds/event/[^/]+$"), 60),
    (re.compile(r"^/odds/status$"), 60),
    (re.compile(r"^/stats/leaders$"), 300),
    (re.compile(r"^/stats/metrics/catalog$"), 3600),
    (re.compile(r"^/stats/compare/(teams|team-vs-league|players)$"), 300),
    (re.compile(r"^/h2h/[A-Z]+/[A-Z]+$"), 600),
    (re.compile(r"^/scores$"), 15),
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
        if request.headers.get("authorization"):
            response.headers["Cache-Control"] = "private, no-store"
            return response

        for pattern, max_age in _RULES:
            if pattern.match(path):
                response.headers["Cache-Control"] = (
                    f"public, max-age={max_age}, s-maxage={max_age}, "
                    f"stale-while-revalidate={max_age // 2}, stale-if-error={max_age}"
                )
                break
        else:
            response.headers["Cache-Control"] = "no-store"
        return response
