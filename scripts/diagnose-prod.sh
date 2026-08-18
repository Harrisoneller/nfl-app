#!/usr/bin/env bash
# Round 2: bisect the hard 500 on /predictions/games.
# Usage: ./scripts/diagnose-prod.sh [API_BASE]
set -u
API="${1:-https://nfl-app-production.up.railway.app}"

probe() {
  local label="$1" path="$2"
  local out
  out=$(curl -s -o /tmp/probe.body -w '%{http_code} %{time_total}s' "$API$path")
  printf '%-46s %s   %s\n' "$label" "$out" "$(head -c 140 /tmp/probe.body | tr -d '\n')"
}

printf '\n\033[1m== A. Does the process have a working DB? ==\033[0m\n'
probe "/live            (no deps)"            "/live"
probe "/ready           (DB ping)"            "/ready"

printf '\n\033[1m== B. Other DB-backed reads — is it ONLY predictions? ==\033[0m\n'
probe "/teams"                                 "/teams"
probe "/scores/scoreboard?limit=5"             "/scores/scoreboard?limit=5"
probe "/news?limit=3"                          "/news?limit=3"
probe "/predictions/elo/current"               "/predictions/elo/current"
probe "/predictions/standings/projected"       "/predictions/standings/projected"

printf '\n\033[1m== C. The failing route, narrowed ==\033[0m\n'
probe "/predictions/games (bare)"              "/predictions/games"
probe "/predictions/games?include_ml=false"    "/predictions/games?include_ml=false"
probe "/predictions/games?week=1&include_ml=false" "/predictions/games?week=1&include_ml=false"
probe "/predictions/games?season=2025&week=1"  "/predictions/games?season=2025&week=1"
probe "/predictions/games?season=2026&week=1"  "/predictions/games?season=2026&week=1"

printf '\n\033[1m== D. Traceback (needs Railway CLI: npm i -g @railway/cli && railway login) ==\033[0m\n'
if command -v railway >/dev/null 2>&1; then
  railway logs 2>/dev/null | grep -iE 'Traceback|Error|Exception|File "' | tail -40
else
  echo "railway CLI not installed — grab the traceback from the Railway dashboard:"
  echo "  Project -> your service -> Deployments -> latest -> View Logs"
  echo "  Filter for: Traceback"
fi
