#!/usr/bin/env bash
# One-shot production warmup after Railway deploy.
# Admin routes require an admin bearer token (401 anonymous, 403 non-admin).
# Usage:
#   ADMIN_BEARER_TOKEN=<access token> ./scripts/warmup-production.sh https://your-app.up.railway.app
# Mint the token with POST /auth/login as a user on ADMIN_EMAILS (or is_admin).
# Do not commit the token.
set -euo pipefail

BASE="${1:?Usage: ADMIN_BEARER_TOKEN=<access token> $0 https://your-railway-url (no trailing slash)}"
BASE="${BASE%/}"

if [[ -z "${ADMIN_BEARER_TOKEN:-}" ]]; then
  echo "ADMIN_BEARER_TOKEN is required. Pass an admin user's access token; do not commit it." >&2
  exit 1
fi

# Header only — the token is never echoed.
auth=(-H "Authorization: Bearer ${ADMIN_BEARER_TOKEN}")

echo "==> $BASE/live"
curl -sf "$BASE/live" | head -c 200
echo

echo "==> $BASE/ready"
curl -sf "$BASE/ready" | head -c 400
echo

echo "==> seed teams"
curl -sf -X POST "${auth[@]}" "$BASE/admin/refresh/teams"
echo

echo "==> refresh schedules (all seasons — may take 1–3 min)"
curl -sf -X POST "${auth[@]}" "$BASE/admin/refresh/schedules"
echo

echo "==> refresh scores + news"
curl -sf -X POST "${auth[@]}" "$BASE/admin/refresh/scores"
echo
curl -sf -X POST "${auth[@]}" "$BASE/admin/refresh/news"
echo

echo "==> refresh odds (needs ODDS_API_KEY on Railway)"
curl -sf -X POST "${auth[@]}" "$BASE/admin/refresh/odds" || echo "(odds skipped — set ODDS_API_KEY if you want lines)"

echo "==> rebuild Elo (needed for predictions / metrics)"
curl -sf -X POST "${auth[@]}" "$BASE/predictions/admin/elo/rebuild" || true
echo

echo "==> data availability"
curl -sf "${auth[@]}" "$BASE/admin/data-availability" | head -c 600
echo

echo "Done. Give analytics warmup ~2 min on first boot, then reload the Vercel site."
