# Runbook

## Fastest path

```bash
./scripts/bootstrap.sh                  # installs deps, creates DB, runs migrations
cp .env.example .env                    # then add GROK_API_KEY
# Terminal A
cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --port 8000
# Terminal B
cd frontend && npm run dev
```

## One-time setup

### 1. Postgres (Homebrew)

```bash
brew install postgresql@16
brew services start postgresql@16
createdb nflapp
```

Verify: `psql -d nflapp -c '\conninfo'`.

### 2. Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 3. Environment

```bash
cp .env.example .env
# Open .env and set at minimum:
#   GROK_API_KEY=...
# (everything else has working defaults)
```

### 4. Migrate

```bash
cd backend
alembic upgrade head
```

### 5. Frontend

```bash
cd frontend
npm install
```

## Running

Two terminals:

```bash
# Terminal A — backend
cd backend && source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

```bash
# Terminal B — frontend
cd frontend
npm run dev
```

Open http://localhost:3000.

API docs at http://localhost:8000/docs.

## Common tasks

**Create a new Alembic migration:**
```bash
cd backend
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

**Switch LLM provider:**
Edit `.env` → `LLM_PROVIDER=anthropic` (and set `ANTHROPIC_API_KEY`). Restart backend.

**Enable Twitter feed:**
Edit `.env` → `ENABLE_TWITTER=true` + `TWITTER_BEARER_TOKEN=...`. Restart backend.

**Force a data refresh:**
```bash
curl -X POST http://localhost:8000/admin/refresh/scores
curl -X POST http://localhost:8000/admin/refresh/schedules
curl -X POST http://localhost:8000/admin/refresh/news
curl -X POST http://localhost:8000/admin/refresh/odds
```

**Sync full-season schedules into Postgres** (needed for scoreboard DB rows; predictions read nflverse live but this warms the `Game` table):

```bash
curl -X POST "http://localhost:8000/admin/refresh/schedules?season=2026"
```

Omit `?season=` to refresh every season in the dropdown (same as the boot scheduler).

## Troubleshooting

**Backend can't connect to Postgres:** Check `DATABASE_URL`. Default assumes Homebrew Postgres on default socket — adjust if you installed differently.

**`nfl-data-py` slow first call:** It downloads ~50MB of parquet files on first use. Subsequent calls hit local cache.

**Odds endpoint returns 429:** You've blown the free tier. Either wait until next month or upgrade. The scheduler caches every 15 min so this should not happen in normal use.

**Grok API errors:** Verify the key at https://console.x.ai. The default model is `grok-2-latest`; older keys may need `grok-beta`.
