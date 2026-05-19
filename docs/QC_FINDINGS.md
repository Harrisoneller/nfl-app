# Quality Control Findings

**Date:** 2026-05-19  
**Scope:** Full-stack structure review (`backend/`, `frontend/`, CI, docs)  
**Intent:** NFL one-stop product — news, scores, stats, fantasy, odds, predictions, AI widgets  
**Verdict:** **Appropriate and sustainable for current scale** with targeted hardening before growth (more features, contributors, or production traffic).

---

## Executive summary

| Area | Grade | Notes |
|------|-------|-------|
| Backend architecture | **A-** | Clear layers, adapters, jobs, config; some large modules |
| Frontend architecture | **B+** | Solid App Router + component domains; a few “god” files |
| Documentation | **A** | ARCHITECTURE, MODELS, RUNBOOK, deploy/security docs |
| Automated quality gates | **B-** | CI exists; frontend lint not gated; thin test coverage |
| Cross-stack contract | **B** | Hand-maintained TS types in `lib/api.ts`; no codegen |

---

## What is working well (keep doing)

### Backend

1. **Layered design matches stated intent** — Adapters → services → routers; routers do not call adapters directly (`docs/ARCHITECTURE.md` is accurate).
2. **Swappable integrations** — `adapters/data`, `adapters/news`, `adapters/llm` each have `base.py` + concrete providers; selection via `Settings`.
3. **Operational baseline** — Structured logging, request IDs, CORS, gzip, rate limits (slowapi), optional Sentry, APScheduler with documented job matrix (`scheduler.py`).
4. **Data model foresight** — `users` table + `MULTI_USER_MODE` toggle; JWT path ready without rewrite.
5. **Performance awareness** — In-process TTL cache (`app.cache`); new **L2 `artifact_cache`** (Postgres-backed) for expensive ML/analytics outputs across restarts — good pattern for prediction-heavy workloads.
6. **Domain documentation** — `docs/MODELS.md` ties prediction math to code paths; rare and valuable for onboarding.

### Frontend

1. **Next.js 14 App Router used intentionally** — Home page is a server component with parallel `Promise.all` + graceful `safe()` fallbacks (`app/page.tsx`).
2. **Component taxonomy** — `components/predictions/`, `betting/`, `charts/`, `widgets/` mirrors product domains.
3. **Single API surface** — `frontend/lib/api.ts` centralizes fetch + shared types; pages/components stay thinner.
4. **UX primitives** — Command palette, team theming (`ThemeProvider`), toast, skeletons — appropriate for a dashboard-style app.
5. **Strict TypeScript** — `strict: true` in `tsconfig.json`; CI runs `next build` as typecheck gate.

### Repo / process

1. **CI workflow** — Backend: ruff + pytest; Frontend: `npm run build` with `NEXT_PUBLIC_API_BASE`.
2. **Docs folder** — Runbook, deploy, privacy, security, AI design — supports coordination without tribal knowledge.

---

## Findings by severity

### P0 — Fix before treating `main` as production-ready

| ID | Finding | Location | Recommendation |
|----|---------|----------|----------------|
| P0-1 | **Frontend ESLint fails locally** (10+ `react/no-unescaped-entities` errors) | `app/teams/[id]/page.tsx`, `PlayerProjections.tsx`, etc. | Fix apostrophes (`&apos;` or rewrite copy). Add `npm run lint` to CI so regressions cannot merge. |
| P0-2 | **Uncommitted model-artifact work** | `model_artifact.py`, `0003_model_artifacts.py`, `artifact_cache.py`, service integrations | Land migration + model in one PR; run `alembic upgrade head` in deploy runbook. Incomplete split risks prod cache misses / import errors. |

### P1 — High value within 1–2 sprints

| ID | Finding | Location | Recommendation |
|----|---------|----------|----------------|
| P1-1 | **No frontend automated tests** | `frontend/` | Add Playwright smoke (home, team page, predictions) or React Testing Library for critical cards. Even 5 tests beat zero. |
| P1-2 | **Backend test surface is thin** | `backend/tests/` (5 modules) | Extend tests for `elo_service`, `predictions_service.predict_week`, `artifact_cache.get_or_compute`, and one router integration per domain. |
| P1-3 | **`lib/api.ts` monolith (~620 lines)** | Types + `req()` + all endpoints | Split: `lib/api/client.ts`, `lib/api/types/*.ts`, re-export from `lib/api/index.ts`. Optionally generate types from OpenAPI later. |
| P1-4 | **God page: team detail** (~750 lines, client-only) | `app/teams/[id]/page.tsx` | Extract tab panels into `components/teams/tabs/*`; keep page as layout + SWR keys only. Improves reviewability and lazy loading. |
| P1-5 | **Inconsistent API response typing (backend)** | `teams.py` uses `response_model`; `predictions.py` returns raw dicts | Add Pydantic schemas for prediction/betting responses or document OpenAPI tags; reduces frontend `any` drift. |
| P1-6 | **No Next.js `error.tsx` / `loading.tsx`** | `app/**` | Add route-level boundaries for team/player/h2h routes so partial API failures don’t white-screen. |

### P2 — Sustainability / scale (schedule when team or traffic grows)

| ID | Finding | Location | Recommendation |
|----|---------|----------|----------------|
| P2-1 | **Large service modules** | `analytics_service.py` (~594 LOC), `player_predictions_service.py`, `predictions_service.py` | Split by concern: `analytics/team_profile.py`, `analytics/player_profile.py`, shared `_percentiles.py`. |
| P2-2 | **`mypy` in dev deps, not CI** | `pyproject.toml` | Add `mypy app` job (incremental, allowlist existing issues) or drop unused dep. |
| P2-3 | **Python version drift** | README 3.11, CI 3.12, local may be 3.13 | Pin `.python-version` or document supported range; align README + CI. |
| P2-4 | **SQLite tests vs Postgres prod** | `conftest.py` | Add optional Postgres integration job in CI (service container) for JSONB / `artifact_cache` upserts. |
| P2-5 | **Mixed data-fetching model** | RSC on home; SWR on team/player pages | Document convention: RSC for static/SEO pages, SWR for interactive dashboards; or standardize on RSC + `loading.tsx` where possible. |
| P2-6 | **`any` usage in TS** | `lib/api.ts`, H2H types, widget payloads | Replace with `unknown` + narrow helpers; generate from OpenAPI when stable. |
| P2-7 | **Scheduler in-process** | `main.py` lifespan | For multi-worker deploy, move jobs to dedicated worker or leader-election to avoid duplicate refreshes. |

### P3 — Nice to have

| ID | Finding | Recommendation |
|----|---------|----------------|
| P3-1 | `next/image` warnings on `TeamLogo` | Use `next/image` with remote patterns for ESPN logos |
| P3-2 | `react-hooks/exhaustive-deps` warnings | `LiveFeed.tsx`, `WidgetRenderer.tsx` — small hook fixes |
| P3-3 | OpenAPI → TypeScript codegen | `openapi-typescript` against `/openapi.json` when schemas stabilize |
| P3-4 | `AGENTS.md` or `CONTRIBUTING.md` | Point agents/contributors to this file + ARCHITECTURE + MODELS |

---

## Structure maps (reference)

### Backend (actual)

```
backend/app/
├── adapters/     # External I/O (data, news, llm)
├── ai/           # Tool-use + widget builder
├── jobs/         # APScheduler
├── middleware/
├── models/       # SQLAlchemy + Alembic
├── routers/      # 17 HTTP modules (thin)
├── schemas/      # Pydantic (partial coverage)
├── services/     # Business logic (21 modules)
└── utils/
```

**Sustainable?** Yes — boundaries are clear. Watch **service file size** and **schema coverage** as prediction features grow.

### Frontend (actual)

```
frontend/
├── app/          # 13 routes (App Router)
├── components/   # Domain-grouped UI (~39 files)
└── lib/          # api, colors, metrics, overlay-url (thin shared logic)
```

**Sustainable?** Yes for ~15 routes. **`app/teams/[id]/page.tsx`** is the main structural debt; split before adding more tabs.

---

## CI / tooling gap analysis

| Check | Backend | Frontend |
|-------|---------|----------|
| Lint | ✅ ruff | ❌ not in CI (`eslint` fails locally) |
| Typecheck | implicit via imports | ✅ `next build` |
| Unit tests | ✅ pytest (5 files) | ❌ none |
| E2E | ❌ | ❌ |
| Migrations | manual in runbook | N/A |

**Recommended CI addition (minimal):**

```yaml
# frontend job, after install
- run: npm run lint
```

---

## In-flight work note (git status)

The following align with **good** architectural direction but should ship atomically:

- `ModelArtifact` model + Alembic `0003`
- `artifact_cache.py` L2 cache
- Service integrations (`analytics`, `predictions`, `backtest`, `awards`, `player_predictions`)
- Frontend dashboard components (`LeaguePulse`, `DivisionStandingCard`, `RecentFormCard`, etc.)

**Coordination:** Backend cache keys/kinds should be documented in `docs/MODELS.md` § cache or a short `docs/CACHING.md`.

---

## Recommended priority queue

1. Fix ESLint errors + add `npm run lint` to CI  
2. Commit and deploy model-artifact migration + cache layer  
3. Split `teams/[id]/page.tsx` into tab components  
4. Split `lib/api.ts` + add 10–15 high-value backend tests  
5. Add `error.tsx` / `loading.tsx` for dynamic routes  
6. Document data-fetching convention (RSC vs SWR) in `docs/ARCHITECTURE.md`

---

## Sign-off

| Reviewer | Role | Date |
|----------|------|------|
| Cursor Agent (QC pass) | Structure / sustainability | 2026-05-19 |
| _Owner_ | Acknowledge / reprioritize | |

_Update this log when items are addressed (check off IDs or link PRs)._
