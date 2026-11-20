#!/usr/bin/env bash
# your-service-name — adaptable service template (see wiki/getting-started.md)
# Backend runner — serve the FastAPI app with Uvicorn.
# ===================================================
#
# Thin, deterministic launcher: apply defaults, validate inputs, print a banner,
# then exec Uvicorn. All configuration comes from the environment (Kubernetes
# ConfigMap / secrets); nothing is hard-coded here.
#
# FastAPI is an ASGI app, so it is served directly by Uvicorn (the ASGI server).
# There is no gunicorn master/worker split: the container runs one Uvicorn process
# and Kubernetes scales PODS (HPA), not processes. Uvicorn logs access and errors
# to stdout and flushes per record, so a request failure surfaces in the pod log
# instead of being swallowed by a buffered master process.
# ===================================================

set -euo pipefail
set -o errtrace
trap 'echo "❌ run_backend failed at ${BASH_SOURCE[0]}:${LINENO}: ${BASH_COMMAND}" >&2' ERR

# ---- Fail-fast helpers --------------------------------------------------------
die() { echo "❌ $*" >&2; exit 1; }

need_cmd() { command -v "$1" >/dev/null 2>&1 || die "'$1' is required but not installed"; }

is_positive_int() { [[ "$1" =~ ^[1-9][0-9]*$ ]]; }

# ---- Configuration (env overridable; defaults are the safe baseline) ----------
BACKEND_ENTRY="${BACKEND_ENTRY:-your_service_name.backend.main:app}"
BACKEND_BIND="${BACKEND_BIND:-0.0.0.0}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
# Seconds Uvicorn drains in-flight requests + lifespan shutdown after SIGTERM
# before force-exiting. Kubernetes gives the pod this long to terminate cleanly.
BACKEND_GRACEFUL_TIMEOUT="${BACKEND_GRACEFUL_TIMEOUT:-30}"
API_VERSION="${API_VERSION:-v1}"

# ---- Validate inputs (fail fast with a clear message) -------------------------
need_cmd uvicorn
is_positive_int "${BACKEND_GRACEFUL_TIMEOUT}" || die "BACKEND_GRACEFUL_TIMEOUT must be a positive integer (got '${BACKEND_GRACEFUL_TIMEOUT}')"

# ---- Banner -------------------------------------------------------------------
echo
echo "▶️  Backend — FastAPI / Uvicorn"
echo "   entry    : ${BACKEND_ENTRY}"
echo "   bind     : ${BACKEND_BIND}:${BACKEND_PORT}"
echo "   graceful : ${BACKEND_GRACEFUL_TIMEOUT}s"
echo "   api      : http://${BACKEND_BIND}:${BACKEND_PORT}/api/${API_VERSION}"
echo "   docs     : http://${BACKEND_BIND}:${BACKEND_PORT}/docs"
echo "   health   : http://${BACKEND_BIND}:${BACKEND_PORT}/api/${API_VERSION}/health"
echo

# ---- Serve --------------------------------------------------------------------
# exec replaces this shell so Uvicorn becomes the container's main process (PID 1
# under tini), receiving SIGTERM directly for clean shutdown.
exec uvicorn "${BACKEND_ENTRY}" \
  --host "${BACKEND_BIND}" \
  --port "${BACKEND_PORT}" \
  --log-level info \
  --timeout-graceful-shutdown "${BACKEND_GRACEFUL_TIMEOUT}"
