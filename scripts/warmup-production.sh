#!/usr/bin/env bash
# One-shot production warmup after Railway deploy.
# Admin routes require an admin bearer token (401 anonymous, 403 non-admin).
#
# The token comes from the environment or from a file. It is written to a
# mode-600 header file and passed as `curl -H @file`, so it never appears
# on the curl command line. Do not commit the token and do not pass it
# as an argument.
#
# Usage:
#   ADMIN_BEARER_TOKEN_FILE=/path/to/token ./scripts/warmup-production.sh https://your-app.up.railway.app
#   ADMIN_BEARER_TOKEN=<access token> ./scripts/warmup-production.sh https://your-app.up.railway.app
#
# ADMIN_BEARER_TOKEN_FILE contains only the raw token (one line).
# Mint it with POST /auth/login as an email on ADMIN_EMAILS.
set -euo pipefail

BASE="${1:?Usage: ADMIN_BEARER_TOKEN_FILE=/path/to/token $0 https://your-railway-url (no trailing slash)}"
BASE="${BASE%/}"

if [[ -n "${ADMIN_BEARER_TOKEN_FILE:-}" ]]; then
  if [[ ! -f "$ADMIN_BEARER_TOKEN_FILE" ]]; then
    echo "ADMIN_BEARER_TOKEN_FILE is not a file. Do not commit the token." >&2
    exit 1
  fi
  token=$(tr -d '[:space:]' < "$ADMIN_BEARER_TOKEN_FILE")
elif [[ -n "${ADMIN_BEARER_TOKEN:-}" ]]; then
  token=$ADMIN_BEARER_TOKEN
else
  echo "Set ADMIN_BEARER_TOKEN or ADMIN_BEARER_TOKEN_FILE (raw token, one line)." >&2
  echo "Do not pass the token as an argument." >&2
  exit 1
fi

if [[ -z "$token" ]]; then
  echo "Admin token is empty." >&2
  exit 1
fi

umask 077
hdr=$(mktemp "${TMPDIR:-/tmp}/statletics-admin-auth.XXXXXX")
chmod 600 "$hdr"
trap 'rm -f "$hdr"' EXIT
# %s so a token that contains % is not interpreted by printf.
printf 'Authorization: Bearer %s\n' "$token" > "$hdr"
unset token

echo "==> $BASE/live"
curl -sf "$BASE/live" | head -c 200
echo

echo "==> $BASE/ready"
curl -sf "$BASE/ready" | head -c 400
echo

echo "==> seed teams"
curl -sf -X POST -H @"$hdr" "$BASE/admin/refresh/teams"
echo

echo "==> refresh schedules (all seasons — may take 1–3 min)"
curl -sf -X POST -H @"$hdr" "$BASE/admin/refresh/schedules"
echo

echo "==> refresh scores + news"
curl -sf -X POST -H @"$hdr" "$BASE/admin/refresh/scores"
echo
curl -sf -X POST -H @"$hdr" "$BASE/admin/refresh/news"
echo

echo "==> refresh odds (needs ODDS_API_KEY on Railway)"
curl -sf -X POST -H @"$hdr" "$BASE/admin/refresh/odds" || echo "(odds skipped — set ODDS_API_KEY if you want lines)"

echo "==> rebuild Elo (needed for predictions / metrics)"
curl -sf -X POST -H @"$hdr" "$BASE/predictions/admin/elo/rebuild" || true
echo

echo "==> data availability"
curl -sf -H @"$hdr" "$BASE/admin/data-availability" | head -c 600
echo

echo "Done. Give analytics warmup ~2 min on first boot, then reload the Vercel site."
