"""The Odds API adapter — free tier 500 req/mo.

Always pull aggressively cached via the scheduler; never call from a
user-facing endpoint without a cache check.

HTTP 401 (rejected or exhausted key) and 402 (payment required) are hard
failures: the response ``error_code`` is logged at error level and
:class:`OddsApiAuthError` is raised so the odds job is recorded as ``error``.
429, 5xx, timeouts, and an empty odds payload are also failures
(:class:`OddsUpstreamError` or a non-ok :class:`OddsFetchResult`). Logs carry
the endpoint path plus ``x-requests-remaining`` and ``x-requests-used`` — never
the request URL, which contains ``apiKey``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import httpx
from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_exponential

from ...config import get_settings
from ...logging_config import get_logger

log = get_logger(__name__)
settings = get_settings()

OddsFetchStatus = Literal[
    "ok", "disabled", "unauthorized", "rate_limited", "error", "degraded",
]

# 401: invalid / deactivated key, or usage quota on some plans.
# 402: payment required / out of credits.
_AUTH_FAILURE_STATUSES = frozenset({401, 402})


class OddsApiAuthError(Exception):
    """The Odds API returned HTTP 401 or 402.

    The scheduled job records this as ``error`` (the same ``DataSyncRun.status``
    every other failed job uses). The auth reason is ``error_code`` on the
    exception and in the run message.
    """

    def __init__(self, status_code: int, error_code: str | None) -> None:
        self.status_code = status_code
        self.error_code = error_code
        code = error_code or "unknown"
        super().__init__(
            f"The Odds API request failed with HTTP {status_code} (error_code={code})"
        )


class OddsUpstreamError(Exception):
    """429, 5xx, timeout, or an empty odds payload.

    Distinct from :class:`OddsApiAuthError` so a bad key is not retried and is
    not treated as a transient upstream miss. The sync run is still ``error``.
    """

    def __init__(self, status: str, message: str | None = None) -> None:
        self.status = status
        detail = message or status
        super().__init__(detail if status in detail else f"{status}: {detail}")


# Statuses that must not be stored as a successful odds job.
UPSTREAM_FAILURE_STATUSES = frozenset({"unauthorized", "rate_limited", "error", "degraded"})


@dataclass(frozen=True)
class OddsFetchResult:
    events: list[dict[str, Any]]
    status: OddsFetchStatus
    message: str | None = None


class TheOddsApiAdapter:
    SPORT = "americanfootball_nfl"

    def __init__(self) -> None:
        self.api_key = settings.odds_api_key.strip()
        self.base = settings.odds_api_base
        self.client = httpx.AsyncClient(timeout=15.0)

    def _enabled(self) -> bool:
        return bool(self.api_key)

    @staticmethod
    def _quota_fields(response: httpx.Response) -> dict[str, str | None]:
        return {
            "x-requests-remaining": response.headers.get("x-requests-remaining"),
            "x-requests-used": response.headers.get("x-requests-used"),
        }

    @staticmethod
    def _error_code(response: httpx.Response) -> str | None:
        try:
            body = response.json()
        except Exception:  # noqa: BLE001 — non-JSON bodies have no error code
            return None
        if not isinstance(body, dict):
            return None
        for key in ("error_code", "errorCode", "code"):
            value = body.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value).strip()
        return None

    def _observe_response(self, response: httpx.Response, path: str) -> None:
        """Log quota headers on every response. 401 and 402 raise."""
        quota = self._quota_fields(response)
        # Path only — the query string holds apiKey and must not be logged here.
        log.info(
            "odds_api_response",
            status_code=response.status_code,
            path=path,
            **quota,
        )
        if response.status_code not in _AUTH_FAILURE_STATUSES:
            return
        error_code = self._error_code(response)
        log.error(
            "odds_api_request_failed",
            status_code=response.status_code,
            error_code=error_code,
            path=path,
            **quota,
        )
        raise OddsApiAuthError(response.status_code, error_code)

    async def _send(self, path: str, params: dict) -> httpx.Response:
        merged = {"apiKey": self.api_key, **params}
        response = await self.client.get(f"{self.base}{path}", params=merged)
        self._observe_response(response, path)
        return response

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(min=1, max=4),
        retry=retry_if_not_exception_type((OddsApiAuthError, OddsUpstreamError)),
    )
    async def _get(self, path: str, params: dict) -> OddsFetchResult:
        if not self._enabled():
            return OddsFetchResult([], "disabled", "ODDS_API_KEY is not set")
        response = await self._send(path, params)
        if response.status_code == 429:
            log.error("odds_api_rate_limited", status_code=response.status_code, path=path)
            return OddsFetchResult([], "rate_limited", "The Odds API rate limit was hit (429)")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            # Do not log the exception: its string includes the full URL and apiKey.
            log.error("odds_api_http_error", status_code=response.status_code, path=path)
            return OddsFetchResult(
                [],
                "error",
                f"The Odds API returned HTTP {response.status_code}",
            )
        data = response.json()
        if not isinstance(data, list):
            return OddsFetchResult([], "error", "Unexpected response shape from The Odds API")
        # An empty 200 from the collection odds endpoint is a degraded pull.
        # The events list is allowed to be empty (offseason / no upcoming games).
        if (
            not data
            and path.endswith("/odds")
            and "/events/" not in path
        ):
            log.error("odds_api_empty", status_code=response.status_code, path=path)
            return OddsFetchResult(
                [],
                "degraded",
                "The Odds API returned an empty odds payload",
            )
        return OddsFetchResult(data, "ok")

    async def fetch_game_odds(
        self,
        markets: tuple[str, ...] = ("h2h", "spreads", "totals"),
        regions: str = "us",
    ) -> list[dict[str, Any]]:
        """Backward-compatible: returns events only (empty on any failure)."""
        return (await self.fetch_game_odds_result(markets=markets, regions=regions)).events

    async def fetch_game_odds_result(
        self,
        markets: tuple[str, ...] = ("h2h", "spreads", "totals"),
        regions: str = "us",
    ) -> OddsFetchResult:
        return await self._get(
            f"/sports/{self.SPORT}/odds",
            {"regions": regions, "markets": ",".join(markets), "oddsFormat": "american"},
        )

    async def fetch_events(self) -> OddsFetchResult:
        """Upcoming/live event ids (cheap — no market cost on The Odds API)."""
        return await self._get(f"/sports/{self.SPORT}/events", {})

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(min=1, max=4),
        retry=retry_if_not_exception_type((OddsApiAuthError, OddsUpstreamError)),
    )
    async def _get_object(self, path: str, params: dict) -> dict[str, Any] | None:
        """Like _get but for endpoints that return one JSON object.

        404/422 stay soft misses (event gone or market unavailable). 401 and 402
        raise :class:`OddsApiAuthError`. 429 and 5xx raise
        :class:`OddsUpstreamError` so a per-event props call cannot be recorded
        as a success. Timeouts propagate and become ``RetryError`` after the
        second attempt.
        """
        if not self._enabled():
            return None
        response = await self._send(path, params)
        if response.status_code in (404, 422):
            log.warning("odds_api_event_fetch_failed", status=response.status_code, path=path)
            return None
        if response.status_code == 429:
            log.error("odds_api_rate_limited", status_code=response.status_code, path=path)
            raise OddsUpstreamError(
                "rate_limited", "The Odds API rate limit was hit (429)",
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            log.error("odds_api_http_error", status_code=response.status_code, path=path)
            raise OddsUpstreamError(
                "error", f"The Odds API returned HTTP {response.status_code}",
            ) from None
        data = response.json()
        return data if isinstance(data, dict) else None

    async def fetch_event_player_props(
        self, event_id: str, markets: tuple[str, ...], regions: str = "us",
    ) -> dict[str, Any] | None:
        """Player-prop odds for ONE event. The Odds API only serves player props
        via the per-event endpoint, and each call bills per unique market —
        callers must budget (see player_props_service)."""
        return await self._get_object(
            f"/sports/{self.SPORT}/events/{event_id}/odds",
            {"regions": regions, "markets": ",".join(markets), "oddsFormat": "american"},
        )

    async def fetch_futures(self, market: str = "outrights") -> list[dict[str, Any]]:
        """Note: futures markets vary by season. Check endpoint availability."""
        result = await self._get(
            f"/sports/{self.SPORT}/odds",
            {"regions": "us", "markets": market, "oddsFormat": "american"},
        )
        return result.events

    async def aclose(self) -> None:
        await self.client.aclose()
