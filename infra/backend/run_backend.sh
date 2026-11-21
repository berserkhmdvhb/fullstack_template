#!/usr/bin/env bash
# your-service-name — adaptable service template (see wiki/getting-started.md)
# Backend runner — serve the FastAPI app with Uvicorn.
# ===================================================
#
# Thin, deterministic launcher: apply defaults, validate inputs, print a banner,
# then exec Uvicorn. All configuration comes from the environment (Kubernetes
# ConfigMap / secrets); nothing is hard-coded here.
#
# FastAPI is an ASGI app, so it is served directly by Uvicorn (the ASGI server):
# one process per container, and Kubernetes scales PODS (HPA), not processes.
#
# Flags worth knowing:
#   --no-proxy-headers  The app resolves the client IP itself (TRUSTED_PROXY_CIDRS,
#                       middleware/client_ip.py). Uvicorn cannot read CF-Connecting-IP, and two
#                       resolvers would be two trust lists, so Uvicorn's is off.
#   --no-server-header  Do not advertise the server software.
#   --no-access-log     The app writes its own JSON request log (LOG_REQUESTS).
#   --timeout-keep-alive  Must stay LONGER than the gateway's upstream idle timeout, or the
#                       proxy can reuse a connection Uvicorn just closed and return a 502.
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
# Seconds an idle keep-alive connection stays open. Keep it above the gateway's upstream idle
# timeout (waf.upstreamIdleTimeoutSeconds in the chart; a test compares the two).
BACKEND_KEEPALIVE_TIMEOUT="${BACKEND_KEEPALIVE_TIMEOUT:-75}"
API_VERSION="${API_VERSION:-v1}"

# ---- Validate inputs (fail fast with a clear message) -------------------------
need_cmd uvicorn
is_positive_int "${BACKEND_GRACEFUL_TIMEOUT}" || die "BACKEND_GRACEFUL_TIMEOUT must be a positive integer (got '${BACKEND_GRACEFUL_TIMEOUT}')"
is_positive_int "${BACKEND_KEEPALIVE_TIMEOUT}" || die "BACKEND_KEEPALIVE_TIMEOUT must be a positive integer (got '${BACKEND_KEEPALIVE_TIMEOUT}')"

# ---- Banner -------------------------------------------------------------------
echo
echo "▶️  Backend — FastAPI / Uvicorn"
echo "   entry    : ${BACKEND_ENTRY}"
echo "   bind     : ${BACKEND_BIND}:${BACKEND_PORT}"
echo "   graceful : ${BACKEND_GRACEFUL_TIMEOUT}s"
echo "   keepalive: ${BACKEND_KEEPALIVE_TIMEOUT}s"
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
  --no-proxy-headers \
  --no-server-header \
  --no-access-log \
  --timeout-keep-alive "${BACKEND_KEEPALIVE_TIMEOUT}" \
  --timeout-graceful-shutdown "${BACKEND_GRACEFUL_TIMEOUT}"
