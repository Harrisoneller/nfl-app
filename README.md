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

**Vercel** (frontend, `frontend/`) + **Railway** (backend + Postgres, `backend/`). Full checklist: [`docs/DEPLOY.md`](docs/DEPLOY.md). Repo is already on GitHub at `Harrisoneller/nfl-app`.

## Documentation

- [Deployment runbook](docs/DEPLOY.md)
- [Architecture overview](docs/ARCHITECTURE.md)
- [Data sources](docs/DATA_SOURCES.md)
- [AI design](docs/AI_DESIGN.md)
- [Runbook](docs/RUNBOOK.md)
