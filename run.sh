#!/usr/bin/env bash
# FINN - one-command start on macOS / Linux. Needs Python 3.10+.
# Creates backend/.venv, installs dependencies, and serves the app + API on port 8000.
set -euo pipefail
cd "$(dirname "$0")/backend"

PY="${PYTHON:-python3}"
[ -x .venv/bin/python ] || { echo "Creating virtual environment..."; "$PY" -m venv .venv; }
echo "Installing dependencies..."
.venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt
[ -f .env ] || cp .env.example .env

if [ ! -f ../frontend/dist/index.html ]; then
  echo "Building the frontend (needs Node 20+)..."
  (cd ../frontend && npm ci && npm run build)
fi

IP="$( (hostname -I 2>/dev/null || ipconfig getifaddr en0 2>/dev/null || true) | awk '{print $1}')"
echo
echo "FINN is starting:"
echo "  this computer : http://localhost:8000"
[ -n "${IP:-}" ] && echo "  your phone    : http://$IP:8000  (same Wi-Fi)"
echo
exec .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
