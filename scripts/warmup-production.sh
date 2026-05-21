#!/usr/bin/env bash
# One-shot production warmup after Railway deploy.
# Usage: ./scripts/warmup-production.sh https://your-app.up.railway.app
set -euo pipefail

BASE="${1:?Usage: $0 https://your-railway-url (no trailing slash)}"
BASE="${BASE%/}"

echo "==> $BASE/live"
curl -sf "$BASE/live" | head -c 200
echo

echo "==> $BASE/ready"
curl -sf "$BASE/ready" | head -c 400
echo

echo "==> seed teams"
curl -sf -X POST "$BASE/admin/refresh/teams"
echo

echo "==> refresh schedules (all seasons — may take 1–3 min)"
curl -sf -X POST "$BASE/admin/refresh/schedules"
echo

echo "==> refresh scores + news"
curl -sf -X POST "$BASE/admin/refresh/scores"
echo
curl -sf -X POST "$BASE/admin/refresh/news"
echo

echo "==> refresh odds (needs ODDS_API_KEY on Railway)"
curl -sf -X POST "$BASE/admin/refresh/odds" || echo "(odds skipped — set ODDS_API_KEY if you want lines)"

echo "==> rebuild Elo (needed for predictions / metrics)"
curl -sf -X POST "$BASE/predictions/admin/elo/rebuild" || true
echo

echo "==> data availability"
curl -sf "$BASE/admin/data-availability" | head -c 600
echo

echo "Done. Give analytics warmup ~2 min on first boot, then reload the Vercel site."
