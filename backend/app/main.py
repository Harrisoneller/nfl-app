"""FastAPI entrypoint.

Wires routers, middleware, lifespan (scheduler start/stop, graceful
shutdown of httpx clients), Sentry init, rate limiting, and CORS.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from .config import get_settings
from .jobs.scheduler import start_scheduler, stop_scheduler
from .logging_config import configure_logging, get_logger
from .middleware.access_log import AccessLogMiddleware
from .middleware.cache_control import CacheControlMiddleware
from .middleware.endpoint_slo import EndpointSLOMiddleware
from .middleware.request_id import RequestIDMiddleware
from .rate_limits import limiter
from .routers import (
    admin,
    ai,
    auth,
    betting,
    fantasy,
    h2h,
    health,
    meta,
    news,
    odds,
    players,
    predictions,
    scores,
    sparky,
    stats,
    teams,
    widgets,
)
from .sentry_init import init_sentry
from .startup_doctor import run as run_startup_doctor

configure_logging()
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    sentry_on = init_sentry()
    from .cache import get_cache

    get_cache()
    log.info(
        "app_starting",
        env=settings.app_env,
        app_role=settings.app_role,
        scheduler=settings.scheduler_enabled,
        boot_warmup=settings.boot_warmup_level if settings.scheduler_enabled else "n/a",
        cache_backend=settings.cache_backend,
        llm_provider=settings.llm_provider,
        sentry=sentry_on,
    )
    # Self-check: surfaces missing-table / missing-key issues in the boot
    # logs so a degraded start is immediately visible rather than mysterious.
    try:
        run_startup_doctor()
    except Exception as e:  # noqa: BLE001
        log.warning("startup_doctor_crashed", error=str(e)[:200])
    if settings.scheduler_enabled:
        start_scheduler()
    else:
        log.info("scheduler_disabled", reason="app_role=web")
    try:
        yield
    finally:
        log.info("app_stopping")
        stop_scheduler()
        await _close_global_clients()
        from .cache import close_cache

        close_cache()


async def _close_global_clients() -> None:
    """Best-effort graceful shutdown of long-lived httpx clients."""
    closers = []
    try:
        from .adapters.data.espn import ESPNScoreboardAdapter  # noqa
        # ESPN client is created per-request; nothing to close globally.
    except Exception:  # noqa: BLE001
        pass
    for c in closers:
        try:
            await c()
        except Exception:  # noqa: BLE001
            pass


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Statletics NFL API",
        version="0.3.0",
        lifespan=lifespan,
    )

    # Middleware order (Starlette executes the LAST-added middleware first):
    #   request → CORS → RequestID → CacheControl → AccessLog → GZip → handler
    # Gzip last so it sees the final body; AccessLog wraps the handler call;
    # CacheControl runs before the response leaves; CORS handles preflights.
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(EndpointSLOMiddleware)
    app.add_middleware(CacheControlMiddleware)
    app.add_middleware(RequestIDMiddleware)
    cors_kwargs: dict = {
        "allow_origins": settings.cors_origin_list,
        "allow_credentials": True,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
        "expose_headers": ["X-Request-ID", "X-Cache-Status"],
    }
    if settings.cors_origin_regex:
        cors_kwargs["allow_origin_regex"] = settings.cors_origin_regex
    app.add_middleware(CORSMiddleware, **cors_kwargs)
    log.info(
        "cors_configured",
        origins=settings.cors_origin_list,
        vercel_regex=bool(settings.cors_origin_regex),
    )

    # Rate limiter (slowapi)
    app.state.limiter = limiter

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
        return JSONResponse(
            status_code=429,
            content={"detail": f"Rate limit exceeded: {exc.detail}"},
        )

    app.include_router(health.router)
    app.include_router(meta.router, prefix="/meta", tags=["meta"])
    app.include_router(auth.router, prefix="/auth", tags=["auth"])
    app.include_router(scores.router, prefix="/scores", tags=["scores"])
    app.include_router(teams.router, prefix="/teams", tags=["teams"])
    app.include_router(players.router, prefix="/players", tags=["players"])
    app.include_router(stats.router, prefix="/stats", tags=["stats"])
    app.include_router(news.router, prefix="/news", tags=["news"])
    app.include_router(odds.router, prefix="/odds", tags=["odds"])
    app.include_router(fantasy.router, prefix="/fantasy", tags=["fantasy"])
    app.include_router(ai.router, prefix="/ai", tags=["ai"])
    app.include_router(widgets.router, prefix="/widgets", tags=["widgets"])
    app.include_router(predictions.router, prefix="/predictions", tags=["predictions"])
    app.include_router(betting.router, prefix="/betting", tags=["betting"])
    app.include_router(sparky.router, prefix="/sparky", tags=["sparky"])
    app.include_router(h2h.router, prefix="/h2h", tags=["h2h"])
    app.include_router(admin.router, prefix="/admin", tags=["admin"])

    return app


app = create_app()
