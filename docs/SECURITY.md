# Security

What we do today + what's left before this is fully production-secure.

## Current posture

- **Auth** — JWT-based scaffold, gated behind `MULTI_USER_MODE`. Default is single-user.
- **Rate limits** — `slowapi` per-IP: 60/min global, 20/hour for AI, 120/min for search.
- **AI cost controls** — per-user + global daily budget; hard 429 when exceeded.
- **Input validation** — Pydantic models on every request body.
- **SQL injection** — SQLAlchemy ORM throughout; raw SQL only in `text("SELECT 1")` for health checks.
- **Secrets** — env-driven; `SECRET_KEY` must be overridden in production (default `"change-me"` is intentionally embarrassing).
- **CORS** — explicit allowlist via `CORS_ORIGINS`.
- **Request tracking** — every request gets an X-Request-ID echoed in logs.
- **Error reporting** — Sentry integration is wired but disabled until you set `SENTRY_DSN`.
- **Dependency hygiene** — pyproject pins minimum versions; `pip-audit` recommended in CI.

## Known gaps (before public launch)

| # | Item | Severity |
|---|---|---|
| 1 | No CSRF protection (we're JWT-only, no cookies, but if you switch to cookie auth this matters) | Medium |
| 2 | No security headers middleware (CSP, HSTS, X-Frame-Options) | High |
| 3 | No request body size limit globally (large multipart could DoS) | Medium |
| 4 | No webhook signing on any inbound integrations (none yet, but plan for it) | Low |
| 5 | No bot detection / CAPTCHA on signup | Medium |
| 6 | Password reset flow not implemented | High (before launch) |
| 7 | No 2FA option | Medium |
| 8 | Tool use in AI loop trusts the model's tool argument values fully — see `app/ai/tools.py` | Low (sandbox: tools only call our own services) |
| 9 | No audit log of admin actions | Low |
| 10 | No data export / right-to-be-forgotten flow | High (GDPR/CCPA) |

## Reporting vulnerabilities

If you find a security issue, please email [your security email] before
posting publicly. We'll respond within 48 hours.

## Recommended pre-launch checklist

1. Generate and set a strong `SECRET_KEY` (`python -c "import secrets; print(secrets.token_urlsafe(64))"`).
2. Set `MULTI_USER_MODE=true` only when you're ready for real signups.
3. Enable Sentry by setting `SENTRY_DSN`.
4. Add a security-headers middleware (Strict-Transport-Security, X-Content-Type-Options, etc.).
5. Run `pip-audit` and `npm audit` in CI.
6. Set up DB backups (see `scripts/backup_db.sh`).
7. Configure SSL/TLS (Let's Encrypt) at the reverse-proxy layer.
8. Set rate limits more conservatively for production (`RATE_LIMIT_DEFAULT=30/minute` is a good start).
