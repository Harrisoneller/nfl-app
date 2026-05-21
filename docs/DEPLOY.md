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
6. **Set environment variables** on the backend service (Variables tab). At minimum:
   ```
   APP_ENV=production
   SECRET_KEY=<run: python -c "import secrets; print(secrets.token_urlsafe(64))" and paste>
   GROK_API_KEY=<your xAI key>

   # Friends testing: keep these conservative
   AI_GLOBAL_DAILY_BUDGET_USD=2.0
   AI_PER_USER_DAILY_BUDGET_USD=0.25
   RATE_LIMIT_DEFAULT=30/minute
   RATE_LIMIT_AI=10/hour

   # Fill in AFTER you've deployed the frontend in step 2:
   CORS_ORIGINS=https://<your-vercel-domain>.vercel.app
   ```
7. The `railway.toml` in `backend/` already sets the start command to run Alembic migrations and boot uvicorn. Railway will pick that up.
8. Railway will assign a public URL (under **Settings → Networking → Public Networking → Generate Domain**). Copy it; you'll need it in step 2. Example: `https://nfl-app-production.up.railway.app`.
9. Test it: `curl https://your-url.up.railway.app/live` → should return `{"ok": true}`.

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
6. **Go back to Railway** and update `CORS_ORIGINS` with that exact URL. Restart the backend service so it picks up the new value. (Required for **team/player** pages — they fetch from the browser; CORS does not affect the homepage server render.)

### 3. Post-deploy bootstrap

Once both are up:

```bash
chmod +x scripts/warmup-production.sh
./scripts/warmup-production.sh https://<your-railway-url>.up.railway.app
```

Or run the curls manually (same script). **Odds** only populate if `ODDS_API_KEY` is set on Railway.

Elo / analytics also warm on a timer (~45–90s after boot). First team-metrics load can take ~30s while nflverse parquet downloads on Railway.

### 4. Custom domain (optional)

- Vercel: **Settings → Domains → Add** (`yourdomain.com`). Update your DNS to Vercel's nameservers or add a CNAME.
- Railway: **Settings → Networking → Custom Domain** (`api.yourdomain.com`). Update CORS_ORIGINS to include the new domain.

### 5. Watch the costs

- Railway: $5/mo for a small instance + $5/mo for the tiny Postgres = $10/mo. They give $5 in starter credit, so the first 2 weeks are effectively free.
- Vercel: Hobby tier is free up to 100 GB bandwidth — way more than friends testing will use.
- xAI (Grok): pay-as-you-go. The cost gates in `app/services/cost_service.py` will hard-stop any user at $0.25/day and the whole app at $2/day, so worst case you spend $60/mo on AI even if 8 friends pound on it constantly.

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
| Auth | Enable real signups | Set `MULTI_USER_MODE=true`. Wire Clerk's Next.js SDK; pass tokens as Bearer headers from the frontend; the backend's `get_current_user` already accepts them. |
| Sentry | Error tracking | Sign up at sentry.io, drop the DSN into `SENTRY_DSN` env var. |
| Backups | Postgres dumps | Railway has a one-click backup feature, or schedule `scripts/backup_db.sh` to S3. |
| Tighten cost caps | Lower per-user budget | Drop `AI_PER_USER_DAILY_BUDGET_USD` to 0.10 or gate AI behind auth-only. |
| Bot/CAPTCHA | Stop scrapers + automated abuse | Cloudflare Turnstile in front of the frontend. |
| Privacy / ToS | Legal must-haves | The stubs in `docs/PRIVACY.md` and `docs/TERMS.md` need a lawyer + your contact info. |
| Stricter security headers | CSP, HSTS | The `vercel.json` already sets X-Frame-Options + X-Content-Type-Options. CSP needs your specific allowlist (Vercel domain, Railway API, fonts.googleapis if used, etc.). |

---

## Common gotchas

- **Site loads but everything is empty (no scores, metrics, schedule, predictions)** — (1) Vercel `NEXT_PUBLIC_API_BASE` missing or set after first build → set to Railway URL and **Redeploy** frontend. (2) Fresh Railway DB never warmed → run `scripts/warmup-production.sh`. (3) Team pages still blank → fix `CORS_ORIGINS` + restart backend. Open DevTools → Network: failed calls to `localhost:8000` mean (1); blocked calls to Railway with CORS errors mean (3).
- **CORS error in browser console after deploy** — `CORS_ORIGINS` doesn't match the Vercel URL exactly (no trailing slash, exact protocol). Fix and restart the backend.
- **Deploy fails: `Connection refused` on localhost:5432, healthcheck `/live` times out** — the backend service has no `DATABASE_URL`. Add a PostgreSQL database to the project, then **reference** its `DATABASE_URL` on the backend service (step 1.5 above). Redeploy.
- **Backend can't connect to Postgres on Railway** — Railway injects `DATABASE_URL` as `postgresql://…`. The app auto-rewrites that to `postgresql+psycopg://…`. If you overrode `DATABASE_URL` manually, use the `+psycopg` form or delete your override so the Postgres reference wins.
- **Backend 500s with `relation "team_elo_ratings" does not exist`** — the Procfile/railway.toml runs `alembic upgrade head`, but if the build fails or you bypassed it, run it manually via `railway run alembic upgrade head` from the Railway CLI.
- **Frontend deploy fails on Vercel with type errors** — `next build` is stricter than `next dev`. If you see errors, run `npm run build` locally first to surface them.
- **Elo never rebuilds** — Railway's container restarts happen periodically; the in-memory cache resets. APScheduler restarts cleanly. If you see no `elo_history_rebuilt` log, hit `POST /predictions/admin/elo/rebuild` manually.
- **xAI / Grok API key not working** — verify at https://console.x.ai/. The current default model is `grok-2-latest`; older keys may need `grok-beta` (set `GROK_MODEL=grok-beta`).

---

## Tearing it down

- Railway: **Settings → Delete Service** (and the Postgres add-on separately).
- Vercel: **Settings → Delete Project**.
- Nothing else to clean up.
