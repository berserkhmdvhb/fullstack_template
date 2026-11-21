#!/usr/bin/env bash
# your-service-name — adaptable service template (see wiki/getting-started.md)
# =============================================
# Backend Runner (FastAPI)
# =============================================
# Behavior:
# - Creates/uses a local venv (.venv)
# - Loads .env if present
# - Developer launcher: Uvicorn with --reload (autoreload on code changes)
# - The image uses infra/backend/run_backend.sh instead (no reload, production flags)
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

echo "Host: ${BACKEND_HOST} | Port: ${BACKEND_PORT} | Reload=on"

echo "▶️  Starting backend (Uvicorn, --reload) at http://localhost:${BACKEND_PORT}"
echo "🔗 API base : http://localhost:${BACKEND_PORT}/api/${API_VERSION}"
echo "🔗 Docs     : http://localhost:${BACKEND_PORT}/docs"
echo "🔗 Health   : http://localhost:${BACKEND_PORT}/api/${API_VERSION}/health"
echo "Press Ctrl+C to stop the server"

# --reload watches the working directory and restarts on a change.
exec uvicorn "${BACKEND_ENTRY}" --host "${BACKEND_HOST}" --port "${BACKEND_PORT}" --reload --log-level info \
  --no-proxy-headers
