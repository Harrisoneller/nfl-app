# Deployment runbook

Two paths. Pick A for friends-testing (always-on, small cost). Pick B for free, your-laptop-must-be-on testing.

---

## Path A — Vercel (frontend) + Railway (backend + Postgres)

Steady-state cost: **~$5–10/mo**. Setup time: **~20 minutes**.

### 1. Backend on Railway

1. Push the repo to GitHub (private is fine).
2. Sign up at https://railway.app (GitHub auth).
3. **New Project → Deploy from GitHub repo** → select your repo.
4. Railway will detect `backend/pyproject.toml` but you need to set the root directory:
   - **Settings → Service → Root Directory** → `backend`
5. **Add PostgreSQL** (required — without this the deploy crashes on `Connection refused`):
   - In the project canvas, click **+ New → Database → PostgreSQL**.
   - Open your **backend service** (`nfl-app`, not the Postgres box) → **Variables**.
   - Click **+ New Variable → Add Reference** (or **Connect** from the Postgres service).
   - Choose the **PostgreSQL** service → select **`DATABASE_URL`** → add.
   - You should see `DATABASE_URL` on the backend service pointing at `${{Postgres.DATABASE_URL}}` (name may vary).
   - **Do not** paste `localhost` or leave `DATABASE_URL` empty on the backend service.
6. **Set environment variables** on the **public API** service (Variables tab). At minimum:
   ```
   APP_ENV=production
   APP_ROLE=web
   BOOT_WARMUP_LEVEL=minimal
   SECRET_KEY=<run: python -c "import secrets; print(secrets.token_urlsafe(64))" and paste>
   # Required for the production boot check. See "Fail-closed production boot" below.
   MULTI_USER_MODE=true
   ADMIN_EMAILS=you@example.com
   GROK_API_KEY=<your xAI key>

   # Friends testing: keep these conservative
   AI_GLOBAL_DAILY_BUDGET_USD=2.0
   AI_PER_USER_DAILY_BUDGET_USD=0.25
   RATE_LIMIT_DEFAULT=30/minute
   RATE_LIMIT_AI=10/hour

   # Canonical site. www is added automatically. statletics.io is also the default
   # of CORS_PRODUCTION_ORIGINS. Preview hosts for CORS_VERCEL_PROJECT (default
   # nfl-app) are allowed as https://nfl-app.vercel.app and https://nfl-app-<preview>.vercel.app.
   CORS_ORIGINS=https://statletics.io
   CORS_VERCEL_PROJECT=nfl-app
   ```
7. **Add a worker service** (Phase A — stops OOM crash-loops on the public URL):
   - In the same Railway project: **+ New → GitHub Repo** → same repo, root directory `backend`.
   - Name it e.g. `nfl-app-worker`. **Do not** attach a public domain.
   - Reference the **same** `DATABASE_URL` from Postgres (step 5).
   - Variables:
     ```
     APP_ENV=production
     APP_ROLE=worker
     BOOT_WARMUP_LEVEL=minimal
     DERIVE_CRON_HOURS_UTC=6,18
     SECRET_KEY=<same as web service>
     MULTI_USER_MODE=true
     ADMIN_EMAILS=you@example.com
     ODDS_API_KEY=<if you use odds>
     ```
   - **Resources:** give the worker **≥ 1 GB RAM** (see [Memory & sizing](#6-memory--sizing)). The web service can stay smaller (~512 MB–1 GB) since it no longer runs heavy boot jobs.
   - Only the **web** service needs `CORS_ORIGINS` and `GROK_API_KEY` if AI runs there; worker does not need CORS.
8. The `railway.toml` in `backend/` runs Alembic migrations then uvicorn on **both** services (migrations are idempotent).
9. Railway will assign a public URL on the **web** service only (**Settings → Networking → Generate Domain**). Example: `https://nfl-app-production.up.railway.app`.
10. Test:
    ```bash
    curl https://your-url.up.railway.app/live
    # → {"ok": true}
    curl https://your-url.up.railway.app/health
    # → app_role: "web", scheduler_enabled: false
    ```
    On the worker, check logs for `scheduler_started` (no public URL required).

#### Fail-closed production boot

The API detects production when any of these is true:

- `APP_ENV=production`.
- `RAILWAY_ENVIRONMENT` or `RAILWAY_ENVIRONMENT_NAME` is `production` (any case), even if `APP_ENV` is explicitly `development`.
- `APP_ENV` is unset or blank, and `RAILWAY_ENVIRONMENT` or `RAILWAY_ENVIRONMENT_NAME` is set to any non-empty value. A Railway preview whose name is not `production` still fails closed. An explicit `APP_ENV=development` opts out of that "any Railway name" rule.

Development and pytest (`APP_ENV=development`, Railway variables unset) stay permissive and still boot with the placeholder secret.

If the check fails, the process logs `production_auth_refused` and exits during startup with `Refusing to start in production:` plus one line per problem. It does not serve traffic. Web and worker both run the check. The log line names the variable; it never prints `SECRET_KEY`.

Production never treats every caller as admin. The only passing posture is multi-user auth with an allowlist:

- `MULTI_USER_MODE=true`.
- `ADMIN_EMAILS` is a comma-separated list of admin emails, for example `ADMIN_EMAILS=you@example.com,other@example.com`. Matching is case-insensitive. Anonymous admin calls get 401. A signed-in email that is not on the list gets 403, even if that row has `is_admin` set.
- `SECRET_KEY` is at least 32 characters, the same value on web and worker, and it passes a basic entropy check (at least 10 distinct characters, not a repeated or low-entropy pattern). The value `change-me`, a blank value, and anything that starts with `change-me` (including the `.env.example` placeholder `change-me-to-a-long-random-string`) are refused. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(64))"`.

These postures refuse to boot:

- `MULTI_USER_MODE=false` with `ADMIN_EMAILS` empty — every caller is the seeded `system@local` admin.
- `ADMIN_EMAILS` set while `MULTI_USER_MODE` is false — bearer tokens are ignored, and `system@local` is not on the allowlist, so nobody can reach admin routes.
- `MULTI_USER_MODE=true` with `ADMIN_EMAILS` empty — no admin list.

| Variable | Required in production | Format |
|---|---|---|
| `APP_ENV` | Set to `production`, or leave unset on Railway | `production`, or unset when a Railway environment variable is present |
| `RAILWAY_ENVIRONMENT` / `RAILWAY_ENVIRONMENT_NAME` | Set by Railway | any non-empty name counts when `APP_ENV` is unset; the value `production` always counts |
| `SECRET_KEY` | Yes | at least 32 characters, not a `change-me` prefix, not low-entropy |
| `MULTI_USER_MODE` | Yes | `true` |
| `ADMIN_EMAILS` | Yes | comma-separated emails, no spaces required |

### 2. Frontend on Vercel

1. Sign up at https://vercel.com (GitHub auth).
2. **New Project** → import the same GitHub repo.
3. **Root Directory** → `frontend`.
4. **Environment Variables** (scope: **Production** at minimum):
   ```
   NEXT_PUBLIC_API_BASE=https://<your-railway-url>.up.railway.app
   ```
   This value is **baked in at build time**. If you add or change it after the first deploy, you **must redeploy** the frontend or every page will still call `localhost:8000` and look empty.
5. Deploy. Vercel gives you a URL like `https://nfl-app-yourname.vercel.app`.
6. **Go back to Railway** and set `CORS_ORIGINS` to the canonical site (`https://statletics.io`; `https://www.statletics.io` is added automatically). Set `CORS_VERCEL_PROJECT` to the Vercel project slug if it is not `nfl-app`. Restart the backend web service. Credentialed browser calls are allowed from those production origins and from that project's preview hosts (`https://<slug>.vercel.app` and `https://<slug>-<preview>.vercel.app`). Other `*.vercel.app` sites are not allowed. (Required for **team/player** pages — they fetch from the browser; CORS does not affect the homepage server render.)

### 3. Post-deploy bootstrap

Once both are up:

Admin routes need an admin bearer token. The warmup script reads it from `ADMIN_BEARER_TOKEN` or from `ADMIN_BEARER_TOKEN_FILE` (a file whose only contents are the raw token). It writes a mode-600 header file and calls curl with `-H @file`, so the token is not placed on the curl command line. Do not commit the token, and do not pass it as an argument.

Mint the token with `POST /auth/login` as an email on `ADMIN_EMAILS`. Production requires `MULTI_USER_MODE=true`, so a token is required and single-user mode will not boot.

```bash
chmod +x scripts/warmup-production.sh
ADMIN_BEARER_TOKEN_FILE=/path/to/token ./scripts/warmup-production.sh https://<your-railway-url>.up.railway.app
# or: ADMIN_BEARER_TOKEN=<access token> ./scripts/warmup-production.sh https://<your-railway-url>.up.railway.app
```

**Odds** only populate if `ODDS_API_KEY` is set on the **worker**. `/admin/sync-status` and the other `/admin/*` reads are admin routes: use the same header file (`curl -H @/path/to/header`) rather than a bare curl. The header file is one line, `Authorization: Bearer <token>`.

Heavy jobs on the **worker**:
- **06:15 + 18:15 UTC** (`DERIVE_CRON_HOURS_UTC`): schedules → materialize nflverse → Elo → profiles → MC → awards
- **03:30 UTC** (`H2H_CRON_HOURS_UTC`): H2H prewarm only

Until the first derive finishes, some pages may load slowly once (lazy compute). `/admin/sync-status` is an admin route. Check it with the same header file the warmup script uses (`curl -sS -H @/path/to/admin.hdr https://your-url.up.railway.app/admin/sync-status`), not a bare curl.

To force a derive immediately after deploy (from your laptop):

```bash
# Hit the worker’s private network isn’t possible from outside Railway —
# use warmup script on the public URL (admin routes run on web; heavy work
# still needs worker). After worker is up, wait for derive cron or temporarily
# set BOOT_WARMUP_LEVEL=full on worker only for one boot (dev-style, high RAM).
```

### 4. Custom domain (optional)

- Vercel: **Settings → Domains → Add** (`yourdomain.com`). Update your DNS to Vercel's nameservers or add a CNAME.
- Railway: **Settings → Networking → Custom Domain** (`api.yourdomain.com`). Update CORS_ORIGINS to include the new domain.

### 5. Watch the costs

- Railway: $5/mo for a small instance + $5/mo for the tiny Postgres = $10/mo. They give $5 in starter credit, so the first 2 weeks are effectively free.
- Vercel: Hobby tier is free up to 100 GB bandwidth — way more than friends testing will use.
- xAI (Grok): pay-as-you-go. The cost gates in `app/services/cost_service.py` will hard-stop any user at $0.25/day and the whole app at $2/day, so worst case you spend $60/mo on AI even if 8 friends pound on it constantly.

### 6. Memory & sizing

The backend is CPU-light but **memory-sensitive**, because its richest features are
computed from nflverse **play-by-play (PBP)** data. The data layer is tuned to keep
this bounded, but it's worth understanding so you size the instance correctly and
don't reintroduce a regression.

**What uses memory.** A single NFL season of PBP is ~50k rows. Loaded naively
(all ~390 columns) that's **700MB–1GB** as a pandas DataFrame. The app avoids this:

- **Column projection** — `app/adapters/data/nfl_data_py_adapter.py` loads only the
  ~20 columns we actually use (`PBP_COLUMNS`), cutting each season's frame to
  **~50–80MB**. If you add a metric that needs a new PBP column, add it to
  `PBP_COLUMNS` *and* the relevant aggregate — otherwise the column silently won't
  be there.
- **Singleflight** — `_team_pbp_aggregates` (the shared path behind predictions,
  betting, h2h, team pages) serializes cold-cache loads, so 10 simultaneous users
  trigger **one** PBP load, not 10. Keep this lock if you refactor.
- **Bounded cache** — `app/cache/` is an LRU-capped in-process tier (`CACHE_MAX_ENTRIES`),
  so it can't grow without limit and pin RAM.
- **Web/worker split** — `APP_ROLE=web` on the public service (no scheduler).
  `APP_ROLE=worker` on a second service runs ingest + derive. Never run heavy
  boot warmups on the URL users hit.
- **Derive on a cron** — analytics / Elo / Monte Carlo / H2H run at
  `DERIVE_CRON_HOURS_UTC` (default 06:15 + 18:15 UTC), not at boot.
- **Minimal boot** — worker boot only seeds teams, players, current-season schedule,
  and news. One PBP frame at a time during derive, not during web startup.

**How much to provision.**

| Instance RAM | Verdict |
|---|---|
| 512 MB | Works for friends-testing, but tight — first PBP download + a Monte Carlo warmup can brush the ceiling. Fine if you don't add more in-memory data. |
| **1 GB** | **Recommended.** Comfortable headroom for the first-load PBP fetch, the season simulator, and a few concurrent users. |
| 2 GB+ | Only needed if you warm many seasons or add heavyweight in-memory datasets. |

Set this in Railway under **Settings → Resources** (or the service's plan). The single
uvicorn worker in `railway.toml` is intentional — **do not add `--workers N`** on a
small instance: each worker is a separate process with its **own** PBP frames and
cache, multiplying memory by N.

**If you see OOM / "ran out of memory" restarts:** check the logs for repeated
`Started server process` lines a few minutes apart (a crash-loop). Confirm you're on
≥1 GB and running a single worker, and that nothing has been changed to load full
(un-projected) PBP or warm multiple seasons at boot.

### 7. Scale reads (Phase D) — Redis + multiple web replicas

**Typed metric tables** (`team_metric_values`, `player_metric_values`) power SQL
leaderboards without pandas:

```bash
curl "https://your-api/stats/leaders?entity=team&metric=off_epa_per_play&limit=10"
curl "https://your-api/stats/leaders?entity=player&position=QB&metric=fantasy_points_ppr"
```

Populated by the worker derive pipeline (after materialize) or manually:

```bash
curl -X POST https://your-api/admin/refresh/metric-index
```

**Optional Redis L1** — when running **more than one web replica**, set on every
**web** service (not required on the worker):

```
CACHE_BACKEND=redis
REDIS_URL=${{Redis.REDIS_URL}}
```

Add a Redis plugin in Railway (**+ New → Database → Redis**), reference `REDIS_URL`
on each `APP_ROLE=web` service. Artifact/profile JSON caches are shared across
replicas; large DataFrames still stay in per-process memory or Postgres.

**Multiple web replicas:** duplicate the `nfl-app` web service (same repo root
`backend`, same `DATABASE_URL`, `APP_ROLE=web`, **no scheduler**). Railway can
scale replicas horizontally; all instances must share Postgres + Redis. Keep **one
worker** service for ingest/derive.

`/ready` reports `redis: ok` when `CACHE_BACKEND=redis`.

---

## Path B — Local backend + ngrok + Vercel (free)

Setup time: **~5 minutes**. Caveat: **your laptop must be running and connected**.

### 1. Install ngrok

```bash
brew install ngrok
# Sign up at https://ngrok.com for a free authtoken
ngrok config add-authtoken <your-token>
```

### 2. Run backend locally

```bash
cd "/Users/HarrisonEller/Documents/Claude/Projects/Principal Developer/nfl-app/backend"
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

In another terminal:
```bash
ngrok http 8000
```

You'll see a forwarding URL like `https://abc123.ngrok-free.app`. Copy it.

### 3. Deploy frontend to Vercel

Same as Path A step 2, but:
```
NEXT_PUBLIC_API_BASE=https://abc123.ngrok-free.app
```

And add an env var on your **local backend** (.env file) before starting it:
```
CORS_ORIGINS=https://<your-vercel-domain>.vercel.app
```

That's it. Your friends hit Vercel, Vercel calls your laptop through ngrok.

**Limitations:**
- ngrok free tier URLs change every restart. Pin one for $8/mo, or paste the new URL into Vercel each time.
- Your Mac has to be awake and your wifi has to be up.
- ngrok free tier has a low-RPS limit (40 req/min). For 3-4 friends testing, it's fine.

---

## Production hardening before going *public* (not friends-testing)

Once you're past friends-testing and want to put this on the open internet:

| Item | What | How |
|---|---|---|
| Auth | Enable real signups | Set `MULTI_USER_MODE=true` and `ADMIN_EMAILS` on **both** the web and worker services. Public pages stay browseable anonymously; per-user features (bets, widgets) become private per account. Admin is granted by `ADMIN_EMAILS` (allowlisted email = admin, everyone else = normal user). The built-in email/password `/auth` flow already issues Bearer tokens the frontend sends; wiring a provider like Clerk is optional. Production refuses to boot in single-user mode, whether or not `ADMIN_EMAILS` is set, and also refuses multi-user mode with an empty allowlist. See [Fail-closed production boot](#fail-closed-production-boot). |
| Sentry | Error tracking | Sign up at sentry.io, drop the DSN into `SENTRY_DSN` env var. |
| Backups | Postgres dumps | Railway has a one-click backup feature, or schedule `scripts/backup_db.sh` to S3. |
| Tighten cost caps | Lower per-user budget | Drop `AI_PER_USER_DAILY_BUDGET_USD` to 0.10 or gate AI behind auth-only. |
| Bot/CAPTCHA | Stop scrapers + automated abuse | Cloudflare Turnstile in front of the frontend. |
| Privacy / ToS | Legal must-haves | The stubs in `docs/PRIVACY.md` and `docs/TERMS.md` need a lawyer + your contact info. |
| Stricter security headers | CSP, HSTS | The `vercel.json` already sets X-Frame-Options + X-Content-Type-Options. CSP needs your specific allowlist (Vercel domain, Railway API, fonts.googleapis if used, etc.). |

---

## Common gotchas

- **Site loads but everything is empty (no scores, metrics, schedule, predictions)** — (1) Vercel `NEXT_PUBLIC_API_BASE` missing or set after first build → set to Railway URL and **Redeploy** frontend. (2) Fresh Railway DB never warmed → run `scripts/warmup-production.sh`. (3) Team pages still blank → fix `CORS_ORIGINS` + restart backend. Open DevTools → Network: failed calls to `localhost:8000` mean (1); blocked calls to Railway with CORS errors mean (3).
- **CORS error in browser console after deploy** — Preflight returns **400** when the browser `Origin` is not allowed. Fix: set `CORS_ORIGINS` or `CORS_PRODUCTION_ORIGINS` to the site URL (e.g. `https://statletics.io` — the API auto-adds `https://www.statletics.io`), no trailing slash, and set `CORS_VERCEL_PROJECT` to the Vercel project slug (default `nfl-app`). Preview hosts for that slug are allowed; other `*.vercel.app` sites are not. Redeploy the **web** service. Check `GET /health` → `cors_origins` lists both apex and www.
- **Deploy fails: `Connection refused` on localhost:5432, healthcheck `/live` times out** — the backend service has no `DATABASE_URL`. Add a PostgreSQL database to the project, then **reference** its `DATABASE_URL` on the backend service (step 1.5 above). Redeploy.
- **Backend can't connect to Postgres on Railway** — Railway injects `DATABASE_URL` as `postgresql://…`. The app auto-rewrites that to `postgresql+psycopg://…`. If you overrode `DATABASE_URL` manually, use the `+psycopg` form or delete your override so the Postgres reference wins.
- **Backend 500s with `relation "team_elo_ratings" does not exist`** — the Procfile/railway.toml runs `alembic upgrade head`, but if the build fails or you bypassed it, run it manually via `railway run alembic upgrade head` from the Railway CLI.
- **Frontend deploy fails on Vercel with type errors** — `next build` is stricter than `next dev`. If you see errors, run `npm run build` locally first to surface them.
- **Elo never rebuilds** — Railway's container restarts happen periodically; the in-memory cache resets. APScheduler restarts cleanly. If you see no `elo_history_rebuilt` log, hit `POST /predictions/admin/elo/rebuild` manually.
- **"Deploy ran out of memory" / backend crashes with >1 user, repeated `Started server process` every few minutes** — an out-of-memory crash-loop. Provision **≥1 GB** (Railway → Settings → Resources) and keep a **single** uvicorn worker (no `--workers N`). See [Memory & sizing](#6-memory--sizing). If it persists after raising RAM, something is loading full (un-projected) play-by-play or warming multiple seasons at boot — check recent changes against `PBP_COLUMNS`, the `_team_pbp_aggregates` singleflight, and `_job_warmup_analytics`.
- **Process exits immediately with `Refusing to start in production`** — the fail-closed boot check failed. Read the lines under that message (they name `SECRET_KEY`, `MULTI_USER_MODE`, and `ADMIN_EMAILS` without printing the secret) and fix the variables on **both** web and worker. See [Fail-closed production boot](#fail-closed-production-boot).
- **Logged in as the admin email but the account shows `system@local` and `/admin` bounces you home** — `MULTI_USER_MODE` is `false` on the web service, so the backend ignores the login JWT and resolves everyone to the seeded `system@local` user. Because that email isn't in `ADMIN_EMAILS`, `/auth/me` returns `is_admin=false` and the admin page redirects. Fix: set `MULTI_USER_MODE=true` on the web (and worker) service and redeploy. Confirm `SECRET_KEY` is set to a stable random value (same on both services) so tokens verify. In production this combination refuses to boot instead of serving.
- **xAI / Grok API key not working** — verify at https://console.x.ai/. The current default model is `grok-2-latest`; older keys may need `grok-beta` (set `GROK_MODEL=grok-beta`).

---

## Tearing it down

- Railway: **Settings → Delete Service** (and the Postgres add-on separately).
- Vercel: **Settings → Delete Project**.
- Nothing else to clean up.
