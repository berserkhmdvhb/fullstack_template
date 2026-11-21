#!/usr/bin/env bash
# your-service-name — adaptable service template (see wiki/getting-started.md)
# =============================================
# Backend Runner (FastAPI)
# =============================================
# Behavior:
# - Creates/uses a local venv (.venv)
# - Loads .env if present
# - Always runs with Gunicorn (UvicornWorker)
# - Optional: GUNICORN_RELOAD=true for dev autoreload
# ---------------------------------------------
# Env (via .env or defaults):
#   APP="your_service_name.backend.main:app"
#   HOST=0.0.0.0
#   PORT=8000
#   GUNICORN_WORKERS=4
#   GUNICORN_TIMEOUT=120
#   GUNICORN_RELOAD=false|true
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
GUNICORN_WORKERS="${GUNICORN_WORKERS:-4}"
GUNICORN_TIMEOUT="${GUNICORN_TIMEOUT:-120}"
GUNICORN_RELOAD="${GUNICORN_RELOAD:-false}"

# Optional GPU env (don't force on non-GPU hosts)
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-}"

# --- activate venv ---
echo "Activating virtual environment..."
# shellcheck disable=SC1091
source .venv/bin/activate

echo "Host: ${BACKEND_HOST} | Port: ${BACKEND_PORT} | Workers=${GUNICORN_WORKERS} | Reload=${GUNICORN_RELOAD}"

# Build Gunicorn args
GUNICORN_ARGS=(
  "${BACKEND_ENTRY}"
  --worker-class "uvicorn.workers.UvicornWorker"
  --bind "${BACKEND_HOST}:${BACKEND_PORT}"
  --timeout "${GUNICORN_TIMEOUT}"
  --access-logfile -
  --error-logfile -
  --log-level info
)

# Add reload if requested (dev convenience)
if [[ "${GUNICORN_RELOAD}" == "true" ]]; then
  GUNICORN_ARGS+=( --reload )
  # When --reload is enabled, using a single worker is typical
  # but we honor GUNICORN_WORKERS if you really want more.
fi

echo "▶️  Starting backend (Gunicorn) at http://localhost:${BACKEND_PORT}"
echo "🔗 API base : http://localhost:${BACKEND_PORT}/api/${API_VERSION}"
echo "🔗 Docs     : http://localhost:${BACKEND_PORT}/docs"
echo "🔗 Health   : http://localhost:${BACKEND_PORT}/api/${API_VERSION}/health"
echo "Press Ctrl+C to stop the server"

# shellcheck disable=SC2086
gunicorn --workers "${GUNICORN_WORKERS}" "${GUNICORN_ARGS[@]}"
