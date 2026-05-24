# OAuth (deferred)

Email + password auth ships in the MVP. Google and Apple sign-in are **not** wired yet.

## Google Sign-In (medium effort)

1. Create OAuth 2.0 credentials in [Google Cloud Console](https://console.cloud.google.com/).
2. Add env vars: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `OAUTH_REDIRECT_URI` (e.g. `http://localhost:8000/auth/google/callback`).
3. Backend: add `authlib` or `httpx` flow — `GET /auth/google` → redirect to Google → `GET /auth/google/callback` → verify ID token, upsert `User` by email, issue same JWT as `/auth/login`.
4. Frontend: "Continue with Google" button linking to `/auth/google` (or Next.js route that proxies).

## Apple Sign-In (higher effort)

Requires Apple Developer membership, Services ID, and JWT client secret rotation. Same callback pattern as Google; Apple returns email only on first consent.

## Alternative: Clerk / Supabase Auth

`docs/DEPLOY.md` mentions Clerk: pass Clerk session JWTs as `Authorization: Bearer` — `get_current_user` in `backend/app/deps.py` would need to validate Clerk JWKS instead of local HS256. Keeps frontend OAuth UX without maintaining providers in-repo.

## Current stack

- Passwords: `passlib` + bcrypt (`backend/app/security.py`)
- Tokens: HS256 JWT in `Authorization: Bearer` (frontend `localStorage`)
- No httpOnly cookies yet — see `docs/SECURITY.md` CSRF note if switching
