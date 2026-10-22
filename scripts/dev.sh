#!/usr/bin/env bash
# Run the back end (port 8000) and the front end dev server (port 5173) together.
set -euo pipefail
cd "$(dirname "$0")/.."
(cd backend && exec uvicorn app.main:app --reload --port 8000) &
BACK=$!
trap 'kill $BACK 2>/dev/null || true' EXIT
(cd frontend && npm run dev)
