# Backend Specification — Statletics NFL API

**Status:** living document · **Last updated:** 2026-06-15 · **App version:** 0.3.0

This is the operational/infrastructure reference for the FastAPI backend. It complements:
- `ARCHITECTURE.md` — high-level layer diagram and product concepts.
- `BACKEND_SCALE_REVIEW.md` — point-in-time hardening audit (findings + fixes).
- `DEPLOY.md` / `RUNBOOK.md` — step-by-step deploy and operate procedures.

If those disagree with this file on infrastructure specifics, fix this file.

---

## 1. Service topology

The same codebase runs in two **roles**, selected by the `APP_ROLE` env var. This is the single most important deployment decision.

| Role | `APP_ROLE` | Scheduler | Public URL | RAM target | Responsibility |
|------|-----------|-----------|-----------|-----------|----------------|
| **web** | `web` | off | yes | 512 MB–1 GB | Serve HTTP. Read from Postgres/cache. No heavy background work. |
| **worker** | `worker` | on | no | **≥1 GB** | Run APScheduler: ingest + the derive pipeline (materialize, Elo, Monte Carlo, profiles, H2H). Writes to Postgres. |

```
                 Vercel (Next.js)
                        │ HTTPS/JSON
                        ▼
        ┌───────────────────────────────┐        ┌──────────────────────┐
        │  web service (APP_ROLE=web)    │        │ worker (APP_ROLE=     │
        │  uvicorn · 1 process · 1 loop  │        │ worker) · APScheduler │
        │  routers→services→adapters     │        │ ingest + derive jobs  │
        └───────┬────────────────┬───────┘        └──────────┬───────────┘
                │                │                            │
                ▼                ▼                            ▼
         ┌────────────┐   ┌─────────────┐            (writes derived data)
         │ PostgreSQL │   │ in-proc L1  │◀───────────────────┘
         │ (shared)   │   │ + L2 (PG)   │
         └────────────┘   └─────────────┘
                │
                ▼ (optional, multi-replica only)
            ┌────────┐
            │ Redis  │  shared cache L1' + global rate-limit store
            └────────┘
```

### Why the split matters
`config.py` defaults `app_role` to `worker`. **If you do not set `APP_ROLE=web` on the public service, it runs the scheduler in the same process that serves users.** The derive pipeline transiently loads play-by-play frames and fits ML models; doing that inside the request-serving process on a small instance causes OOM crash-loops (`Started server process` repeating minutes apart in logs). Always set `APP_ROLE=web` on anything with a public domain.

### The $5 single-service reality
The documented happy path is two services (~$10/mo). If you must run one service on the $5 plan, set `APP_ROLE=web` so the public box never runs heavy jobs, keep `BOOT_WARMUP_LEVEL=minimal`, and drive the derive pipeline from an **external** scheduler (GitHub Actions / Railway cron) hitting an admin endpoint during off-peak hours. You trade always-on background derive for a stable user-facing process. Running one service as `worker` is the discouraged path — it works until traffic + a derive cycle collide.

---

## 2. Process & concurrency model

- **One uvicorn worker per service, intentionally.** Do **not** add `--workers N` on a small instance — each worker is a separate process with its own PBP frames and L1 cache, multiplying memory by N. Scale with RAM or (once Redis is on) replicas, not in-process workers.
- **One event loop.** `async def` endpoints share it; blocking work (nfl-data-py, feedparser) is offloaded via `run_in_executor` so it never stalls the loop.
- **Sync endpoints** (`def`, ~71 of them) run in Starlette's AnyIO threadpool — default **40 tokens**, i.e. ~40 concurrent blocking requests before queueing.
- **Capacity intuition:** hundreds of users are fine *while responses are cache hits* (served in ms). The risk is a slow/cold endpoint occupying threads + DB connections long enough to starve others. Mitigations below (caching, singleflight, budgets) exist specifically to keep the slow path rare.

---

## 3. Request lifecycle & middleware stack

Middleware is registered in `main.py`. Starlette executes the **last-added first**, so the effective order around a request is:

```
request
  → CORS                (preflight, attaches CORS headers to every response incl. 429)
  → RequestIDMiddleware (assigns X-Request-ID, used in logs)
  → SlowAPIMiddleware   (enforces rate limits — see §4)
  → CacheControlMiddleware
  → EndpointSLOMiddleware (per-endpoint latency snapshots)
  → AccessLogMiddleware (structured access log around the handler)
  → GZipMiddleware      (compresses final body ≥1 KB)
  → handler
```

`SlowAPIMiddleware` sits inside RequestID (so a throttled request still gets an ID and is logged) and inside CORS (so the browser can read the 429). The liveness/readiness probes opt out — see §4.

---

## 4. Rate limiting

Engine: **slowapi**, keyed per client IP (`get_remote_address`). Configured in `rate_limits.py`, enforced by `SlowAPIMiddleware` in `main.py`.

### Enforcement model
`default_limits=[RATE_LIMIT_DEFAULT]` applies to **every route** because `SlowAPIMiddleware` is installed. (Before 2026-06-15 the middleware was missing, so the default was a silent no-op and only decorated routes were limited — fixed.) Specific routes tighten further with explicit decorators.

| Scope | Setting | Default | Applies to |
|-------|---------|---------|-----------|
| Global default | `RATE_LIMIT_DEFAULT` | `60/minute` | all routes via middleware |
| AI | `RATE_LIMIT_AI` | `20/hour` | `/ai/*`, `/fantasy` advice (decorated) |
| Auth | `RATE_LIMIT_AUTH` | `10/minute` | `/auth/*` (decorated) |
| Search | `RATE_LIMIT_SEARCH` | `120/minute` | reserved for search-style routes |

**Exempt:** `/live`, `/ready`, `/health` carry `@limiter.exempt` so platform healthchecks are never throttled.

**Response shapes (cosmetic inconsistency, both HTTP 429):**
- Middleware-enforced (global default): body `{"error": "Rate limit exceeded: ..."}`.
- Decorator-enforced (AI/auth): body `{"detail": "Rate limit exceeded: ..."}` via the custom handler in `main.py`.
Clients should branch on the **429 status code**, not the body shape.

### Storage & scaling
Counters live **in-process** by default — correct and fast for a single replica, but per-process, so two replicas would each allow the full limit. When `CACHE_BACKEND=redis`, the limiter automatically points its storage at `REDIS_URL`, making limits global across replicas. No code change needed to scale — just set the two env vars.

---

## 5. Caching — two tiers (+ optional shared tier)

| Tier | Where | Lifetime | Purpose |
|------|-------|----------|---------|
| **L1** | in-process LRU (`app/cache/memory.py`, bounded by `CACHE_MAX_ENTRIES`) | TTL, lost on restart | hot serving; can't grow unbounded / pin RAM |
| **L2** | Postgres `model_artifacts` (`artifact_cache.get_or_compute`) | immutable or TTL | survives restarts/deploys; the durable warm cache |
| **L1′** | Redis (`TieredCache` + `RedisTTLCache`), optional | TTL | shared L1 across replicas (JSON-safe values only; DataFrames stay process-local) |

### L2 invalidation strategy (the clever part)
- **Completed-season data** is written immutable (`valid_until=None`) — computed once, served forever.
- **Live-season data** is keyed on the season's *completed-game count*. When a game goes final the count changes → new key → automatic recompute, with no manual invalidation.
- **Expensive-but-volatile** results (Monte Carlo, backtests) carry a medium TTL so a restart still benefits.

### Memory discipline (do not regress)
- **Column projection:** `nfl_data_py_adapter.PBP_COLUMNS` loads ~20 of ~390 PBP columns → ~50–80 MB/season instead of ~1 GB. Adding a metric that needs a new column means adding it to `PBP_COLUMNS` *and* the aggregate.
- **Singleflight:** `_team_pbp_aggregates` serializes cold loads so N concurrent users trigger **one** PBP fetch, not N. Keep this lock through any refactor.

---

## 6. Database & connections

- **Engine:** Postgres via `psycopg` v3, SQLAlchemy 2.0. Alembic migrations run on deploy (`alembic upgrade head`).
- **Pool:** `pool_size=20` + `max_overflow=10` = **30 connections per process**. `pool_pre_ping=True`, `pool_recycle=1800s`.
- **Statement timeout:** on the `web` role only, a server-side `statement_timeout` (`DB_STATEMENT_TIMEOUT_MS`, default 15 000 ms) caps any single query so a runaway can't pin a pooled connection and cascade into pool exhaustion. The `worker` role is **exempt** (its derive/materialize queries legitimately run long). Postgres-only; ignored on sqlite (tests).
- **Connection budget rule:** `connections_used ≈ processes × (pool_size + max_overflow)`. Keep this under Postgres `max_connections`. With one web process = 30; adding a worker and/or replicas multiplies it. Verify the Postgres plan's limit before scaling.
- **Sessions:** `get_db` dependency yields a request-scoped session and always closes it. `autoflush=False`, `expire_on_commit=False`.

---

## 7. Scheduler & jobs (worker role)

APScheduler (`AsyncIOScheduler`, UTC) starts only when `scheduler_enabled` (i.e. `APP_ROLE=worker`). All jobs use `coalesce=True, max_instances=1` so a backlog collapses to one run and jobs never overlap.

| Job | Trigger (default) | Work |
|-----|-------------------|------|
| boot seed/players/schedule/news | a few seconds after boot (`BOOT_WARMUP_LEVEL=minimal`) | seed teams, sync players, current-season schedule, news |
| `derive_pipeline` | cron `DERIVE_CRON_HOURS_UTC` (06:15, 18:15) | schedules → materialize nflverse → metric index → Elo (6 seasons) → profiles → Monte Carlo → awards → H2H |
| `h2h_nightly` | cron `H2H_CRON_HOURS_UTC` (03:30) | H2H prewarm only |
| `scores` | every `SCHEDULE_SCORES_SECONDS` (30 s), live period only | ESPN scoreboard refresh + Sparky auto-settle |
| `news` | `SCHEDULE_NEWS_SECONDS` (300 s live / 24 h off) | RSS/Reddit refresh |
| `odds` | cron `ODDS_REFRESH_HOURS_UTC` (01:00, 13:00) | The Odds API pull (budget-guarded) + Sparky slate rebuild |
| `players_daily`, `schedules_daily` | every 24 h | roster + schedule refresh |
| `model_lifecycle_weekly` | Mon 07:45 | weekly model lifecycle |
| `endpoint_slo_snapshot` | every 5 min | flush latency SLO snapshot to DB |
| `cache_vacuum_weekly` | every 7 d | drop L2 artifacts older than 7 d |

**Odds budget guard:** The Odds API free tier = 500 credits/mo; each pull costs (markets × regions) credits. Only the scheduled job calls the API; everything user-facing reads the DB snapshot. `ODDS_MIN_REFRESH_HOURS` floors the cadence and `ODDS_LOOKAHEAD_DAYS` skips offseason pulls.

---

## 8. Graceful degradation

- **Request budgets:** `request_budget_service.run_with_budget` wraps expensive composites (e.g. H2H, 25 s budget) with `asyncio.wait_for` + fallback tiers (`primary` → `stale` → `summary_fallback`). `asyncio.shield` keeps a cold compute alive to populate L2 even if the caller's budget trips, so the *next* request is fast instead of repeatedly cancelling mid-flight.
- **Cache failures are absorbed:** if L2 is down or an artifact won't deserialize, code falls through to recompute. A cache read never takes down a request.
- **Health:** `/live` (no deps — liveness, used by the Railway healthcheck) vs `/ready` (DB ping + Redis ping when enabled — returns degraded checks). Keep healthcheck pointed at `/live` so a DB blip doesn't kill the container.

---

## 9. Observability

- **Structured logging** (structlog) with `X-Request-ID` correlation.
- **Sentry** error reporting when `SENTRY_DSN` is set (`SENTRY_TRACES_SAMPLE_RATE` for tracing; 0.0 = errors only).
- **Endpoint SLOs** captured by middleware and snapshotted to the DB every 5 min.
- **AI cost ledger** at `/admin/cost-summary`; sync job status at `/admin/sync-status`.

---

## 10. Security & cost guardrails

- **AI spend caps:** `AI_GLOBAL_DAILY_BUDGET_USD` + `AI_PER_USER_DAILY_BUDGET_USD` hard-stop LLM usage by design.
- **Auth:** JWT (HS256) when `MULTI_USER_MODE=true`; otherwise everyone resolves to the seeded `system@local` user. Admin routes gated by `ADMIN_EMAILS` allowlist (works even in single-user mode), falling back to the DB `is_admin` flag.
- **CORS:** explicit `CORS_ORIGINS` (apex+www auto-expanded) plus, when `CORS_ALLOW_VERCEL_REGEX=true`, `https://.*\.vercel\.app`. ⚠️ With `allow_credentials=True` that regex lets any Vercel-hosted site make credentialed calls — tighten to your project's preview pattern before treating auth as load-bearing.
- **Secrets** come only from env / `.env`; nothing references env vars outside `config.py`.

---

## 11. Scaling playbook

Apply in order as load grows:

1. **Single web + worker, ≥1 GB worker.** Set `APP_ROLE` correctly. Rate limiting on (done). Statement timeout on (done). — *handles low hundreds of users comfortably if cache-warm.*
2. **Right-size RAM** before adding replicas (vertical first — preserves cache hit rate and the single-process memory model).
3. **Load test** top endpoints (`k6`/`locust`) at target concurrency to find the threadpool/pool knee; tune `DB_POOL_SIZE` and, if DB allows, the AnyIO threadpool.
4. **Turn on Redis** (`CACHE_BACKEND=redis`, `REDIS_URL`) — this makes the cache *and* rate limits shared, unlocking horizontal scale.
5. **Add web replicas** (same repo, `APP_ROLE=web`, shared Postgres + Redis). Keep exactly **one** worker.
6. **Watch Postgres `max_connections`** against `replicas × 30` and raise the DB plan as needed.

---

## 12. Configuration reference (infrastructure-relevant)

| Env var | Default | Notes |
|---------|---------|-------|
| `APP_ENV` | `development` | `production` in prod |
| `APP_ROLE` | `worker` | **set `web` on public service** |
| `BOOT_WARMUP_LEVEL` | `minimal` | `none` \| `minimal` \| `full` |
| `DATABASE_URL` | local | `postgresql://` auto-rewritten to `+psycopg` |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `DB_POOL_TIMEOUT` | 20 / 10 / 30 | per process |
| `DB_STATEMENT_TIMEOUT_MS` | `15000` | web role only; 0 disables; Postgres only |
| `CACHE_BACKEND` | `memory` | `redis` for multi-replica |
| `REDIS_URL` | localhost | used for cache **and** rate-limit store when `redis` |
| `CACHE_MAX_ENTRIES` | `2048` | L1 LRU bound |
| `RATE_LIMIT_DEFAULT` | `60/minute` | global, via middleware |
| `RATE_LIMIT_AI` / `RATE_LIMIT_AUTH` / `RATE_LIMIT_SEARCH` | 20/hour / 10/minute / 120/minute | decorated routes |
| `MULTI_USER_MODE` | `false` | enables JWT auth |
| `ADMIN_EMAILS` | "" | admin allowlist |
| `CORS_ORIGINS` / `CORS_ALLOW_VERCEL_REGEX` | localhost / true | browser origins |
| `DERIVE_CRON_HOURS_UTC` / `H2H_CRON_HOURS_UTC` / `ODDS_REFRESH_HOURS_UTC` | 6,18 / 3 / 1,13 | worker crons |
| `SENTRY_DSN` / `SENTRY_TRACES_SAMPLE_RATE` | "" / 0.0 | observability |
| `AI_GLOBAL_DAILY_BUDGET_USD` / `AI_PER_USER_DAILY_BUDGET_USD` | 10 / 1 | hard cost caps |

---

## 13. Change log

- **2026-06-15** — Installed `SlowAPIMiddleware` so `RATE_LIMIT_DEFAULT` is actually enforced on all routes (previously a no-op); exempted `/live`,`/ready`,`/health`. Wired the slowapi store to Redis when `CACHE_BACKEND=redis`. Added web-role-only `statement_timeout` (`DB_STATEMENT_TIMEOUT_MS`). See `BACKEND_SCALE_REVIEW.md` for rationale.
