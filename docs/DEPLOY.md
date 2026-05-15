# Deployment runbook

The cheapest path to a public deploy that survives traffic.

## Minimum viable production

- **DNS + domain** — Cloudflare or Namecheap, $10–15/year.
- **Frontend** — Vercel (free tier covers 100 GB/mo bandwidth; very generous for an MVP).
- **Backend** — Railway, Fly.io, or Render. ~$5–20/mo for a small instance + managed Postgres.
- **Postgres** — Railway/Render include this in their tier; alternatively Supabase free tier (500 MB, 2 GB egress/mo).
- **Reverse proxy + SSL** — Cloudflare in front of everything; free SSL.
- **Sentry** — free tier 5k events/mo (more than enough early on).

Estimated baseline cost: **$20–30/mo** without paid data sources.

## Environment variables (production)

```bash
APP_ENV=production
SECRET_KEY=<generate with: python -c "import secrets; print(secrets.token_urlsafe(64))">

DATABASE_URL=postgresql+psycopg://<user>:<pw>@<host>/<db>?sslmode=require

MULTI_USER_MODE=true                     # flip when ready for public

LLM_PROVIDER=grok
GROK_API_KEY=<your key>

# Cost controls — tighten for prod
AI_GLOBAL_DAILY_BUDGET_USD=5.0
AI_PER_USER_DAILY_BUDGET_USD=0.25

# Rate limits — production should be conservative
RATE_LIMIT_DEFAULT=30/minute
RATE_LIMIT_AI=10/hour
RATE_LIMIT_SEARCH=60/minute

# Observability
SENTRY_DSN=<from sentry.io>
LOG_LEVEL=INFO

# CORS — your domain only
CORS_ORIGINS=https://yourdomain.com,https://www.yourdomain.com
```

## Backend startup command

```bash
# Single worker, fine up to ~50 RPS
uvicorn app.main:app --host 0.0.0.0 --port $PORT

# Multi-worker (when you outgrow single)
uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 4
```

Note: with multiple workers, **APScheduler runs in each worker** and will
schedule jobs in each. The cleanest fix is to disable the in-process scheduler
in the API workers and run a separate scheduler service. See "Splitting the
scheduler" below.

## Frontend deploy (Vercel)

```bash
cd frontend
vercel --prod
```

Set `NEXT_PUBLIC_API_BASE=https://api.yourdomain.com` in Vercel's project settings.

## Database

### Migrations

Run before each deploy:
```bash
cd backend
alembic upgrade head
```

### Backups

See `scripts/backup_db.sh`. Run nightly via cron or your platform's scheduled job feature:
```bash
0 4 * * * /path/to/scripts/backup_db.sh
```

## Splitting the scheduler (when you scale past 1 worker)

The current scheduler is in-process. For multiple workers:

1. Set `DISABLE_SCHEDULER=true` in the API workers' env.
2. Run a separate process that imports `app.jobs.scheduler` and calls
   `start_scheduler()`. Same image, different command.
3. Or switch to a dedicated scheduler like `arq` (already in pyproject) with
   Redis-backed queues.

## Health checks

Your platform should point at:
- **Liveness:** `GET /live` — returns 200 if the process is up.
- **Readiness:** `GET /ready` — returns 200 only if DB ping succeeds.

## Rollback

Migrations are reversible (`alembic downgrade -1`). Application rollback is
your platform's "redeploy previous version" button.

## Cost dashboard

After deploy, check `GET /admin/cost-summary` (gate behind admin auth before
public). Watch the global cost-per-day; alerting on >50% of budget is the
move once you have real users.
