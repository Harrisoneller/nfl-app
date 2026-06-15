# Backend Infrastructure Review — Can it handle 100s of users?

**Date:** 2026-06-15
**Scope:** `nfl-app/backend` (FastAPI + SQLAlchemy + APScheduler on Railway), reviewed against the goal of serving hundreds of concurrent users reliably.
**Verdict:** The codebase is genuinely well-engineered — the data layer, caching, and graceful-degradation patterns are better than most production hobby projects. But there is a gap between the *intended* deployment (two services, ≥1 GB RAM, documented in `DEPLOY.md`) and what a single **$5 Railway plan** can actually run. The biggest risks are **(1) rate limiting is not actually enforced on data endpoints**, and **(2) the API and the heavy scheduler likely share one undersized process**. Neither is a code rewrite — both are a few lines of config plus a deployment change.

---

## TL;DR — ranked actions

| # | Severity | Issue | Status |
|---|----------|-------|--------|
| 1 | **High** | `SlowAPIMiddleware` is never installed → `RATE_LIMIT_DEFAULT` is a no-op; only 4 routes are limited. One client can hammer expensive endpoints unbounded. | ✅ **Fixed 2026-06-15** — middleware installed, probes exempted, Redis-backed store when scaled. Verified end-to-end. |
| 2 | **High** | `APP_ROLE` unset → defaults to `worker`, so the API process also runs the derive pipeline (Elo rebuild, Monte Carlo, PBP materialize). On a single 512 MB box this is the OOM crash-loop the docs explicitly warn about. | ⏳ **Action on you** — verify `APP_ROLE=web` on the live Railway service (dashboard, not local `.env`). |
| 3 | **Medium** | Single uvicorn worker → ~40 concurrent blocking-request ceiling (AnyIO threadpool) and 30 DB connections. Fine when cache-hot, stalls for everyone when a slow endpoint saturates it. | ⏳ Tuning + load test |
| 4 | **Medium** | In-memory cache + in-memory rate-limit means there is **no working horizontal-scale path until Redis is enabled**. Adding replicas today fragments cache and limits. | ⏳ Enable Redis when scaling (limiter now auto-uses it) |
| 5 | **Medium** | No Postgres `statement_timeout` → one runaway query pins a pooled connection until it finishes. | ✅ **Fixed 2026-06-15** — `DB_STATEMENT_TIMEOUT_MS` (web role only, 15 s default). |
| 6 | **Low** | CORS `allow_origin_regex = https://.*\.vercel\.app` + `allow_credentials=True` lets *any* Vercel-hosted site make credentialed calls. | ⏳ Tighten regex |
| 7 | **Low** | Both web and worker run `alembic upgrade head` on deploy; simultaneous starts can race on the alembic version row. | ⏳ Gate migrations to one service |

---

## What is already strong (keep doing this)

This is not a project that needs rescuing. The following are correct and should be preserved through any refactor:

- **Memory-bounded PBP loading.** `nfl_data_py_adapter.py` projects to ~20 columns (`PBP_COLUMNS`), cutting a season frame from ~1 GB to ~50–80 MB. This is the single most important thing keeping the app inside a small instance.
- **Singleflight on cold loads.** `_team_pbp_aggregates` serializes concurrent cold-cache loads, so 10 simultaneous users trigger one PBP fetch, not 10. This is what makes a traffic spike survivable.
- **Two-tier cache done right.** L1 in-process LRU (`CACHE_MAX_ENTRIES`-bounded so it can't pin RAM) + L2 Postgres artifact store (`artifact_cache.get_or_compute`) that survives restarts and deploys. Completed-season data is written immutable; live data carries short TTLs keyed on completed-game count so it self-invalidates. Excellent.
- **Request budgets with fallback tiers.** `run_with_budget` + `asyncio.shield` lets a cold H2H compute keep running (and populate L2) even when the caller's 25 s budget trips, instead of being cancelled mid-flight and never caching. This was clearly learned the hard way and is the right pattern.
- **Heavy work offloaded correctly.** `nfl_data_py` and `feedparser` calls go through `run_in_executor`, keeping the event loop free during blocking I/O.
- **Liveness vs readiness split.** `/live` (no deps, used by Railway healthcheck) vs `/ready` (DB + Redis ping). Correct — a DB blip won't kill the container.
- **Cost gates on AI.** Global + per-user daily USD ceilings in `cost_service` mean a runaway LLM bill is capped by design.
- **Operational hygiene:** structured logging, Sentry hooks, gzip, request-ID middleware, endpoint SLO snapshots, `restartPolicyMaxRetries`, idempotent migrations, append-only odds snapshots.

---

## Detailed findings

### 1. Rate limiting is not enforced on data endpoints — **High**

`main.py` configures the limiter (`app.state.limiter = limiter`) and registers the `RateLimitExceeded` handler, but **never adds `SlowAPIMiddleware`**. In slowapi, `default_limits` only take effect through that middleware (or a per-route `@limiter.limit` decorator). Only `auth`, `ai` (×2), and `fantasy` carry explicit decorators.

**Consequence:** `/teams`, `/players`, `/stats`, `/odds`, `/predictions`, `/sparky`, `/h2h`, `/scores` have **no rate limit at all**. The `RATE_LIMIT_DEFAULT=30/minute` you set in `DEPLOY.md` does nothing. A single scraper, a buggy frontend retry loop, or one bad actor can issue unbounded requests to the most expensive endpoints — exactly the failure mode that matters at "hundreds of users."

**Fix:**
```python
from slowapi.middleware import SlowAPIMiddleware
app.add_middleware(SlowAPIMiddleware)   # applies default_limits to every route
```
Then exempt the probes so Railway healthchecks are never throttled (decorate `/live`, `/ready` with `@limiter.exempt`, or give them a dedicated high limit). On a single replica the in-memory limiter store is fine; when you add replicas, point slowapi at Redis (`storage_uri=settings.redis_url`) so limits are global rather than per-replica.

### 2. API and scheduler likely share one undersized process — **High**

`config.py` defaults `app_role` to `worker`, and the local `.env` does **not** set `APP_ROLE`. When `app_role == "worker"`, `scheduler_enabled` is true and `start_scheduler()` runs the full derive pipeline **in the same process that serves HTTP**: schedules → nflverse materialize → metric index → 6-season Elo rebuild → profiles → Monte Carlo → awards → H2H, plus a 30 s live-scores loop.

`DEPLOY.md` is explicit that this must be split — a public `APP_ROLE=web` service (scheduler off) and a separate `APP_ROLE=worker` with **≥1 GB RAM** — and warns the combined mode causes "OOM crash-loops on the public URL." A $5 single-service Railway plan can't run both halves comfortably: the derive pipeline transiently loads PBP frames and fits ML models in the same address space as your request handlers.

**What to verify first:** check the **Railway dashboard variables on the live service** (not the local `.env`). If `APP_ROLE=web` is set there and a second worker service exists, you're fine and this drops to informational. If not, this is your top reliability risk.

**Fix (two options):**
- **Correct:** run the documented two services (~$10/mo). Web stays small; worker gets ≥1 GB and owns all scheduled work.
- **Budget stopgap (stay at $5, one service):** set `APP_ROLE=web` so the public box never runs heavy jobs, keep `BOOT_WARMUP_LEVEL=minimal`, and drive the derive pipeline from an **external cron** (GitHub Actions / Railway cron) that hits an admin endpoint, or temporarily flip a worker on only during off-peak. You lose always-on background derive but you stop OOM risk on the user-facing path. The existing `_web_role_h2h_warmup` already shows the intended web-role behavior.

### 3. Concurrency ceiling: one event loop, ~40 threads, 30 DB connections — **Medium**

`railway.toml` runs a single uvicorn worker (intentional — `--workers N` multiplies PBP/cache memory by N on a small box, so don't). That means:
- **Async endpoints** share one event loop — fine, since blocking work is offloaded.
- **Sync endpoints** (71 of them) run in Starlette's AnyIO threadpool, default **40 tokens** → ~40 concurrent blocking requests before queueing.
- **DB pool** = `pool_size=20 + max_overflow=10 = 30` connections.

For hundreds of users this is usually fine **because most responses are cache hits served in milliseconds** — 40 threads churn through fast requests quickly. The danger is a *slow* endpoint (cold H2H, an uncached prediction) occupying threads/connections long enough to starve everyone else. Mitigations already exist (L1/L2 cache, singleflight, budgets), but there's little headroom.

**Recommendations:**
- Confirm Railway Postgres `max_connections` comfortably exceeds 30 (× number of processes). If you ever add a worker service or replicas, set per-role pool sizes so the sum stays under the DB limit.
- Consider raising the AnyIO threadpool to ~64 *only if* you confirm DB pool and Postgres connections can back it (`anyio.to_thread.current_default_thread_limiter().total_tokens`). Don't raise threads above what the DB pool can serve, or you just move the queue.
- Add a lightweight load test (e.g. `k6`/`locust`) hitting your top 5 endpoints at 100–300 concurrent to find the real knee before users do.

### 4. No engaged horizontal-scale path until Redis is on — **Medium**

`CACHE_BACKEND` defaults to `memory`. The Redis tier (`TieredCache` + `RedisTTLCache`) is well-built but inactive. As long as you run **one** web replica this is correct and fastest. But it means the answer to "scale to hundreds" is currently *vertical only* — you can't safely add replicas, because each would have a cold independent L1 cache and (per finding 1) independent rate-limit counters.

**When you outgrow one box:** add a Railway Redis, set `CACHE_BACKEND=redis` + `REDIS_URL` on every web replica, point slowapi storage at the same Redis, keep one worker. The code already supports this; it's a config flip. Until then, scale by giving the single box more RAM rather than more replicas.

### 5. No statement timeout on Postgres — **Medium**

A pathological query (or a lock wait) holds one of your 30 connections indefinitely, and under load that cascades into pool exhaustion. Add a server-side guard at the engine level:
```python
engine = create_engine(
    settings.database_url,
    connect_args={"options": "-c statement_timeout=15000"},  # 15s, tune per endpoint
    ...
)
```
Pair with `pool_pre_ping=True` (already set ✓).

### 6. Over-broad CORS — **Low**

`cors_origin_regex = r"https://.*\.vercel\.app"` with `allow_credentials=True` means *any* site hosted on `*.vercel.app` can make credentialed requests to your API. For a hobby beta this is low-risk, but tighten it to your own project's preview pattern (e.g. `https://nfl-app-[a-z0-9-]+\.vercel\.app`) before you treat auth as load-bearing.

### 7. Migration race on deploy — **Low**

Both services' start commands run `alembic upgrade head`. Two containers booting at once can race on the `alembic_version` row. Migrations are idempotent so damage is unlikely, but cleaner to run migrations only on the web service (or a one-shot release step) and have the worker just wait/boot.

---

## Suggested sequence

1. **Verify live Railway config** — confirm `APP_ROLE` and whether a worker service exists. This determines whether finding 2 is critical or already handled.
2. **Add `SlowAPIMiddleware`** (+ exempt `/live`,`/ready`). Smallest change, biggest abuse-resistance gain.
3. **Add `statement_timeout`.**
4. **Decide the topology:** two services (~$10, the documented happy path) or web-only + external cron (stay at $5, lose always-on derive).
5. **Load test** the top endpoints at target concurrency; tune threadpool/pool to the measured knee.
6. **Tighten CORS.**
7. **Keep Redis in your back pocket** — flip it on the day you add a second replica, not before.

None of this is a rewrite. The hard engineering (memory-bounded data layer, tiered caching, graceful degradation) is already done well; what's missing is enforcing the limits you already configured and matching the deployment to the documented design.
