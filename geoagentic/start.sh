#!/usr/bin/env bash
# Starts backend (port 8000) and frontend (port 5173). Ctrl+C stops both.
set -e
cd "$(dirname "$0")"
(cd backend && pip install -q -r requirements.txt && uvicorn app.main:app --reload --port 8000) &
BACK=$!
trap "kill $BACK 2>/dev/null" EXIT
cd frontend && npm install --no-audit --no-fund && npm run dev
