#!/usr/bin/env bash
# your-service-name — adaptable service template (see wiki/getting-started.md)
# =============================================
# Backend Runner (FastAPI)
# =============================================
# Behavior:
# - Creates/uses a local venv (.venv)
# - Loads .env if present
# - Runs the database migrations before serving (the app never migrates itself)
# - Developer launcher: the same launcher as the image, `python -m your_service_name.backend.serve`
#   (one process, no auto-reload: restart to pick up a change; serves the metrics on METRICS_PORT)
# ---------------------------------------------
# Env (via .env or defaults):
#   APP="your_service_name.backend.main:app"
#   BACKEND_HOST=0.0.0.0
#   BACKEND_PORT=8000
#   CUDA_VISIBLE_DEVICES (optional; unset by default)
# =============================================
# If not running under bash (e.g., /bin/sh), re-exec with bash.
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi
set -euo pipefail

echo "Starting Backend..."
echo "=========================================="

# --- venv bootstrap: the locked dependencies (uv.lock), once ---
if [ ! -f ".venv/.deps_installed" ]; then
  echo "Installing the locked dependencies..."
  # the pinned uv (scripts/tools.sh install uv) when present, else the one on PATH
  if [ -x .tools/bin/uv ]; then PATH="$PWD/.tools/bin:$PATH"; fi
  uv sync --locked
  touch .venv/.deps_installed
fi

# --- load .env if present ---
if [ -f ".env" ]; then
  set -a
# shellcheck source=/dev/null
  . ./.env
  set +a
fi

# --- config defaults ---
API_VERSION="${API_VERSION:-v1}"
BACKEND_ENTRY="${BACKEND_ENTRY:-your_service_name.backend.main:app}"
BACKEND_HOST="${BACKEND_HOST:-0.0.0.0}"
BACKEND_PORT="${BACKEND_PORT:-8000}"

# Optional GPU env (don't force on non-GPU hosts)
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-}"

# --- activate venv ---
echo "Activating virtual environment..."
# shellcheck disable=SC1091
source .venv/bin/activate

# The app does not migrate at startup: bring the database to head first (a no-op when current).
echo "Applying database migrations..."
python -m your_service_name.backend.db.run_migrations

echo "Host: ${BACKEND_HOST} | Port: ${BACKEND_PORT} | Metrics: ${METRICS_PORT:-9102}"

echo "▶️  Starting backend (the serve module) at http://localhost:${BACKEND_PORT}"
echo "🔗 API base : http://localhost:${BACKEND_PORT}/api/${API_VERSION}"
echo "🔗 Docs     : http://localhost:${BACKEND_PORT}/docs"
echo "🔗 Health   : http://localhost:${BACKEND_PORT}/api/${API_VERSION}/health"
echo "Press Ctrl+C to stop the server (no auto-reload: restart to pick up a change)"

# The same launcher as the image (python -m your_service_name.backend.serve): the fixed Uvicorn
# options, the graceful timeout and the Prometheus metrics server (METRICS_PORT). It reads its
# settings from the environment.
export BACKEND_ENTRY BACKEND_PORT
export BACKEND_BIND="${BACKEND_BIND:-${BACKEND_HOST}}"
exec python -m your_service_name.backend.serve
