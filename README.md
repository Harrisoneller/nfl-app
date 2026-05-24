# Statletics NFL

News, scores, stats, fantasy analytics, sportsbook odds, and an AI assistant that can answer questions and generate custom views on demand — all in one place.

## Stack

- **Frontend:** Next.js 14 (App Router) + TypeScript + Tailwind
- **Backend:** Python 3.11 + FastAPI + SQLAlchemy + Alembic
- **DB:** PostgreSQL (local Homebrew install)
- **Background jobs:** APScheduler
- **AI:** Grok (xAI) via a swappable `LLMProvider` interface — Anthropic / OpenAI implementations stubbed for one-line swap

## Quick start

See [`docs/RUNBOOK.md`](docs/RUNBOOK.md) for full setup.

```bash
# Install Postgres (one-time)
brew install postgresql@16
brew services start postgresql@16
createdb nflapp

# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp ../.env.example ../.env  # fill in GROK_API_KEY at minimum
alembic upgrade head
uvicorn app.main:app --reload --port 8000

# Frontend (in a new terminal)
cd frontend
npm install
npm run dev   # http://localhost:3000
```

## Project layout

```
nfl-app/
├── backend/        FastAPI service, data adapters, AI layer, scheduler
├── frontend/       Next.js app, widget renderer, theming
├── docs/           ARCHITECTURE, DATA_SOURCES, AI_DESIGN, RUNBOOK
└── .env.example
```

## Deploy (friends testing)

**Vercel** (frontend, `frontend/`) + **Railway** (backend + Postgres, `backend/`). Use two Railway services: `APP_ROLE=web` (public API) and `APP_ROLE=worker` (scheduler). Full checklist: [`docs/DEPLOY.md`](docs/DEPLOY.md).

## Optional accounts (auth)

Browsing teams, scores, news, and odds works **without** signing in. Accounts are optional and used for saved widgets, AI chat history, and per-user data when multi-user mode is on.

| Variable | Default | Purpose |
|----------|---------|---------|
| `MULTI_USER_MODE` | `false` | `false`: all API routes use the seeded `system@local` user (single-tenant). `true`: JWT required on protected routes (`/widgets`, `/ai`, etc.). |
| `SECRET_KEY` | (required in prod) | Signs JWT access tokens |
| `JWT_EXPIRE_MINUTES` | `10080` (7 days) | Token lifetime |

**Enable multi-user locally:** set `MULTI_USER_MODE=true` in repo-root `.env`, restart the backend, register at `/register`, then use the app signed in. Public pages still work anonymously; widget/AI endpoints need a Bearer token (stored in `localStorage` by the frontend).

**API:** `POST /auth/register`, `POST /auth/login`, `GET/PATCH /auth/me`, `POST /auth/change-password` — Bearer token on protected calls.

**OAuth (Google / Apple):** not implemented in this MVP. See [docs/OAUTH.md](docs/OAUTH.md) for what would be needed.

## Documentation

- [Deployment runbook](docs/DEPLOY.md)
- [Architecture overview](docs/ARCHITECTURE.md)
- [Data sources](docs/DATA_SOURCES.md)
- [AI design](docs/AI_DESIGN.md)
- [Runbook](docs/RUNBOOK.md)
