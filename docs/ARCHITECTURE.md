# Architecture

## High-level

```
┌──────────────────┐       HTTPS/JSON       ┌────────────────────────┐
│  Next.js (TS)    │ ─────────────────────▶ │  FastAPI (Python)      │
│  App Router      │                         │  Routers → Services    │
│  Tailwind        │ ◀─────────────────────  │  Services → Adapters   │
│  Widget renderer │                         │  Adapters → Sources    │
└──────────────────┘                         └────────┬───────────────┘
                                                      │
                                       ┌──────────────┼─────────────────────┐
                                       ▼              ▼                     ▼
                                ┌─────────────┐ ┌──────────┐         ┌─────────────┐
                                │ PostgreSQL  │ │ Scheduler│         │  Adapters   │
                                │ SQLAlchemy  │ │APScheduler│         │ ESPN/nfl-py │
                                │ + Alembic   │ │ jobs      │         │ Sleeper     │
                                └─────────────┘ └──────────┘          │ TheOddsAPI  │
                                                                       │ RSS/Reddit  │
                                                                       │ Twitter*    │
                                                                       │ LLM (Grok)  │
                                                                       └─────────────┘
                                                                       (* feature-flagged)
```

## Layers

**Adapters** — Thin, swappable wrappers around external sources. Every domain (data, news, llm) has a `base.py` with the abstract interface and one concrete impl per provider. Provider selection is config-driven.

**Services** — Business logic that combines adapters with caching and DB persistence. Routers never call adapters directly.

**Routers** — Thin HTTP layer; depends on services via FastAPI `Depends`.

**Models** — SQLAlchemy ORM (Postgres). Alembic for migrations.

**Jobs** — APScheduler tasks that periodically refresh hot data (scores, news, odds) into the DB so the UI is fast.

**AI** — LLM provider behind an interface, with tool-use over the same services (so the AI can call the same APIs the UI does), and a widget builder that emits a JSON spec the frontend renders.

## Auth model

Single-user today, multi-tenant tomorrow. The DB has a real `users` table from day one and every domain row has a `user_id` foreign key (where applicable, e.g. saved widgets, chat history). When `MULTI_USER_MODE=false`, the app uses a hardcoded `system` user. Flip the flag, expose the `/auth/*` routes in the UI, and you're a real consumer product without a migration.

## Theming

Tailwind + CSS variables. Each NFL team has a token bundle (`--team-primary`, `--team-secondary`, `--team-accent`). On a team page the layout sets these variables and components inherit them, so the same widget code automatically takes on the team's identity.

## Widget system

The AI doesn't render UI directly — it returns a `WidgetSpec` (typed JSON) describing what to display: which data source, what shape, what filters. The frontend's `<WidgetRenderer />` interprets specs and uses the same component primitives the rest of the app uses. This keeps the UI consistent and lets users save AI-generated views to their dashboard.

## Cost & feature flags

The cap is <$50/mo, so:
- Live scores via ESPN public JSON (free, unofficial)
- Stats/PBP via `nfl-data-py` (free, github)
- Odds via The Odds API free tier (500 req/mo) — cached aggressively by the scheduler
- News via RSS + Reddit (free)
- Social: Twitter adapter exists but requires `ENABLE_TWITTER=true`. Off by default.
- LLM: Grok billed per token; tools cache results so the model rarely re-fetches
