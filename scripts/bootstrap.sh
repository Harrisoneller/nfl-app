#!/usr/bin/env bash
# One-shot setup for new clones. Idempotent.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"

echo "→ Postgres check…"
if ! command -v psql >/dev/null 2>&1; then
  echo "  Postgres not found. Install: brew install postgresql@16 && brew services start postgresql@16"
  exit 1
fi
if ! psql -lqt | cut -d \| -f 1 | grep -qw nflapp; then
  echo "  Creating DB 'nflapp'…"
  createdb nflapp
fi

echo "→ Backend venv + deps…"
cd "$ROOT/backend"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -e .
echo "→ Migrations…"
alembic upgrade head

echo "→ Frontend deps…"
cd "$ROOT/frontend"
if [ ! -d node_modules ]; then
  npm install --no-audit --no-fund
fi

echo
echo "✅ Setup complete."
echo
echo "Next:"
echo "  1. cp .env.example .env  (and set GROK_API_KEY at minimum)"
echo "  2. Terminal A:  cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --port 8000"
echo "  3. Terminal B:  cd frontend && npm run dev"
echo "  4. Open http://localhost:3000"
