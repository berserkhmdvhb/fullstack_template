#!/usr/bin/env bash
# your-service-name — adaptable service template (see wiki/getting-started.md)
# Central Entrypoint for Local & Container runs
# Modes: backend | frontend | all
# - Starts backend (Uvicorn) and/or frontend (Streamlit / Next.js)
# - 'all' starts backend + frontend together and health-waits each
# - the Next.js server serves its own static assets (/_next/static, public/)
# - FastAPI's /api, /docs, /openapi.json are proxied to the backend by the
#   Next.js frontend proxy (see frontend/proxy.ts); there is no separate
#   reverse proxy.
# - Prints developer-friendly URLs while keeping binds on 127.0.0.1
# Supports containers: /app/src/** (the backend image does not ship this script; it runs `python -m your_service_name.backend.serve`)
# Supports local run: ./src/**
# =============================================================================

# Re-exec with bash if needed
if [[ -z "${BASH_VERSION:-}" ]]; then exec bash "$0" "$@"; fi
set -euo pipefail
set -o errtrace
trap 'echo "❌ ERR at ${BASH_SOURCE}:${LINENO}: ${BASH_COMMAND}" >&2' ERR

export PS4='+ $(date "+%H:%M:%S") ${BASH_SOURCE}:${LINENO}:${FUNCNAME[0]:-main} > '
[[ "${TRACE:-0}" == "1" ]] && set -x

umask 027
# The inherited PATH comes before the system dirs: in the image it starts with the app venv (/app/.venv/bin),
# which must win over the base image's python in /usr/local/bin.
export PATH="/home/appuser/.local/bin:${PATH}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# ---- Load .env if present -------------------------------------------
# Only set variables that are NOT already defined (e.g. by docker-compose).
# This lets docker-compose environment: entries take precedence over .env.
#
# Skip .env loading in Kubernetes (secrets/config come from K8s, not files).
# shellcheck disable=SC1091
if [[ -f ".env" && -z "${KUBERNETES_SERVICE_HOST:-}" ]]; then
  echo "[entrypoint] Loading .env file (existing env vars take precedence)." >&2
  while IFS='=' read -r key value; do
    [[ -z "$key" || "$key" =~ ^[[:space:]]*# ]] && continue
    key="${key%%[[:space:]]*}"
    [[ -z "${!key+x}" ]] && export "$key=$value"
  done < .env
elif [[ -n "${KUBERNETES_SERVICE_HOST:-}" ]]; then
  echo "[entrypoint] Running in Kubernetes — skipping .env file (using K8s env vars)." >&2
fi

# ---- Defaults --------------------------------------------------------
SERVICE_MODE="${SERVICE_MODE:-backend}"
API_VERSION="${API_VERSION:-v1}"

BACKEND_ENTRY="${BACKEND_ENTRY:-your_service_name.backend.main:app}"
BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_BIND="${BACKEND_BIND:-${BACKEND_HOST}}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
BACKEND_GRACEFUL_TIMEOUT="${BACKEND_GRACEFUL_TIMEOUT:-30}"
BACKEND_RELOAD="${BACKEND_RELOAD:-false}"

FRONTEND_IMPL="${FRONTEND_IMPL:-react}"   # frontend implementation (react)
FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"
FRONTEND_BIND="${FRONTEND_BIND:-${FRONTEND_HOST}}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"

# Standalone server
FRONTEND_REACT_SERVER="${FRONTEND_REACT_SERVER:-/app/frontend/.next/standalone/server.js}"

FRONTEND_ROOT="${FRONTEND_ROOT:-/app/frontend}"

# ---- CLI -------------------------------------------------------------
SHOW_HELP=false
NO_WAIT=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) SHOW_HELP=true; shift ;;
    -m|--mode) SERVICE_MODE="$2"; shift 2 ;;
    --no-wait) NO_WAIT=true; shift ;;
    --frontend-impl) FRONTEND_IMPL="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

if $SHOW_HELP; then
  cat <<'EOF'
Usage: entrypoint.sh [--mode backend|frontend|all] [--frontend-impl react|streamlit] [--no-wait]

Environment (overridable via .env or -e):
  SERVICE_MODE         backend|frontend|all (default: backend)
  API_VERSION          v1 (default: v1)

  # Backend
  BACKEND_ENTRY        your_service_name.backend.main:app
  BACKEND_HOST         127.0.0.1
  BACKEND_PORT         8000
  BACKEND_GRACEFUL_TIMEOUT   30
  BACKEND_RELOAD             false (hot reload: a developer option, refused when ENV is production)
  METRICS_PORT               9102 (the Prometheus metrics server)

  # Frontend
  FRONTEND_IMPL        react (frontend implementation)
  FRONTEND_HOST        127.0.0.1
  FRONTEND_PORT        3000
  FRONTEND_REACT_SERVER  /app/frontend/.next/standalone/server.js (container)
                         ./frontend/.next/standalone/server.js    (local)
  EXTRA_CA_PEM         <path to custom root CA PEM>

  FRONTEND_ROOT        /app/frontend
EOF
  exit 0
fi

# ---- Resolve Hosts -----
if [[ "${SERVICE_MODE}" == "all" ]]; then
  BACKEND_HOST=127.0.0.1
  FRONTEND_HOST=127.0.0.1
fi

# ---- Utilities --------------------------------------------------------
need_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "❌ Missing '$1'"; exit 1; }; }

connect_host() {
  local h="${1:-}"
  if [[ -z "$h" || "$h" == "0.0.0.0" ]]; then echo "127.0.0.1"
  elif [[ "$h" == "::" ]]; then echo "[::1]"
  else echo "$h"; fi
}

wait_for_200() {
  local url="$1" name="${2:-service}" curl_opts="${3:-}" timeout="${4:-60}"
  local deadline=$(( $(date +%s) + timeout ))
  echo "⏳ Waiting for ${name} at ${url} ..."
  while [[ $(date +%s) -lt $deadline ]]; do
    # shellcheck disable=SC2086  # curl_opts is a space-separated flag list; word-splitting is intentional
    if curl -fsS ${curl_opts} --max-time 3 "$url" >/dev/null 2>&1; then
      echo "✅ ${name} is healthy."; return 0
    fi
    sleep 1
  done
  echo "❌ Timeout waiting for ${name} at ${url}" >&2
  return 1
}

# True when a path is missing, or a symlink whose target does not exist
# ("dangling"). Used to detect the ABSOLUTE .next/static symlink the Next.js
# standalone bundle bakes in at build time: it resolves on the build host but
# dangles once the bundle is copied elsewhere (a CI artifact restored into
# a different container, a deploy), which 404s every /_next/static client chunk.
_is_unresolved() {
  local p="$1"
  if [[ -L "$p" ]]; then [[ -e "$p" ]] || return 0; return 1; fi
  [[ ! -d "$p" ]]
}
resolve_ca_bundle() {
  local p="${EXTRA_CA_PEM:-}"
  [[ -z "$p" ]] && return 0
  if   [[ -f "$p" ]]; then :;
  elif [[ -f "/app/${p#./}" ]]; then p="/app/${p#./}";
  elif [[ -f "./${p#./}" ]]; then p="./${p#./}";
  else echo "⚠️ EXTRA_CA_PEM set but file not found: ${EXTRA_CA_PEM}" >&2; return 0; fi
  export SSL_CERT_FILE="$p" REQUESTS_CA_BUNDLE="$p" NODE_EXTRA_CA_CERTS="$p"
  echo "[entrypoint] Using custom CA bundle: $p"
}

# Autodetect standalone server path (search multiple locations). Only the modes that start the
# frontend need it: a backend-only run must not depend on a frontend build existing.
if [[ "${FRONTEND_IMPL}" == "react" && ( "${SERVICE_MODE}" == "frontend" || "${SERVICE_MODE}" == "all" ) ]]; then
  if [[ ! -f "${FRONTEND_REACT_SERVER}" ]]; then
    # Search order: explicit path, container path, local path, find in common locations
    for candidate in \
      "/app/frontend/.next/standalone/server.js" \
      "./frontend/.next/standalone/server.js" \
      "${FRONTEND_ROOT:-/app/frontend}/.next/standalone/server.js"; do
      if [[ -f "$candidate" ]]; then
        FRONTEND_REACT_SERVER="$candidate"
        break
      fi
    done

    # Last resort: find server.js in any .next/standalone directory
    if [[ ! -f "${FRONTEND_REACT_SERVER}" ]]; then
      # `|| true`: find exits non-zero when /app is absent, which pipefail and the ERR trap would turn into an abort
      local_found="$(find . /app -name "server.js" -path "*/.next/standalone/*" -type f 2>/dev/null | head -n1 || true)"
      if [[ -n "$local_found" ]]; then
        FRONTEND_REACT_SERVER="$local_found"
      fi
    fi
  fi
fi

# ---- Next runtime validator ---------------------------------------------------
check_next_runtime() {
  local server_path="$1"
  local root; root="$(cd "$(dirname "$server_path")/../.." && pwd)"
  local missing=0
  local warn_only=0

  for f in \
    "$root/.next/build-manifest.json" \
    "$root/.next/BUILD_ID" \
    "$root/.next/routes-manifest.json" \
    "$root/.next/prerender-manifest.json" \
    "$root/.next/app-build-manifest.json" \
    "$root/.next/app-path-routes-manifest.json" \
    "$root/.next/images-manifest.json" \
    "$root/.next/required-server-files.json" \
    "$root/.next/standalone/server.js" \
    "$root/.next/static" \
    "$root/.next/server" \
    "$root/public"
  do
    if [[ ! -e "$f" ]]; then
      case "$f" in
        "$root/.next/app-build-manifest.json"|"$root/.next/app-path-routes-manifest.json")
          echo "ℹ️ (warn) Missing optional App Router manifest: $f" >&2
          warn_only=$((warn_only+1))
          ;;
        *)
          echo "⚠️ Missing Next runtime artifact: $f" >&2
          missing=$((missing+1))
          ;;
      esac
    fi
  done

  if [[ $missing -gt 0 ]]; then
    echo "❌ Next runtime is incomplete; ensure 'next build' ran and artifacts were copied." >&2
    return 1
  fi
  return 0
}

# ---- Banners ------------------------------------------------------------------
print_backend_banner() {
  local host="$1" port="$2" c; c="$(connect_host "$host")"
  echo; echo "▶️ Backend bind: http://${host}:${port}"
  echo " - API    : http://${c}:${port}/api/${API_VERSION}"
  echo " - Docs   : http://${c}:${port}/docs"
  echo " - Health : http://${c}:${port}/api/${API_VERSION}/health"
}

print_frontend_banner() {
  local host="$1" port="$2" c; c="$(connect_host "$host")"
  echo; echo "▶️ Frontend bind: http://${host}:${port} (impl=${FRONTEND_IMPL})"
  echo " - UI     : http://${c}:${port}/"
}

# ---- Log context --------------------------------------------------------------
echo "[entrypoint] SERVICE_MODE=${SERVICE_MODE}"
echo "[entrypoint] FRONTEND_IMPL=${FRONTEND_IMPL}"
echo "[entrypoint] PWD=$(pwd)"
echo "[entrypoint] Backend : http://127.0.0.1:${BACKEND_PORT} (ENTRY=${BACKEND_ENTRY})"
echo "[entrypoint] Frontend: http://127.0.0.1:${FRONTEND_PORT} (impl=${FRONTEND_IMPL})"

resolve_ca_bundle

# ---- Tool checks --------------------------------------------------------------
case "${SERVICE_MODE}" in
  backend)
    need_cmd python
    need_cmd curl
    ;;

  frontend)
    need_cmd curl
    need_cmd node
    [[ -f "${FRONTEND_REACT_SERVER}" || -f "./frontend/.next/standalone/server.js" ]] || {
      echo "❌ Next.js standalone server.js not found. Did you run 'next build'?" >&2
      exit 1
    }
    ;;

  all)
    need_cmd curl
    need_cmd python
    need_cmd node
    # Fallback: resolve local ./frontend/ path if container default not found
    if [[ ! -f "${FRONTEND_REACT_SERVER}" && -f "./frontend/.next/standalone/server.js" ]]; then
      FRONTEND_REACT_SERVER="./frontend/.next/standalone/server.js"
    fi
    [[ -f "${FRONTEND_REACT_SERVER}" ]] || {
      echo "❌ Not found: ${FRONTEND_REACT_SERVER}" >&2
      exit 1
    }
    ;;

  *)
    echo "❌ Unknown SERVICE_MODE=${SERVICE_MODE}. Valid modes: backend, frontend, all" >&2
    exit 1
    ;;
esac

echo "▶ Entrypoint context:"
echo " Backend : ${BACKEND_HOST}:${BACKEND_PORT}"
echo " Frontend: ${FRONTEND_HOST}:${FRONTEND_PORT}  (impl=${FRONTEND_IMPL})"
echo " API_VERSION=${API_VERSION}"
[[ "${FRONTEND_IMPL}" == "react" ]] && echo " React server: ${FRONTEND_REACT_SERVER}"

# ---- Starters ---------------------------------------------------------------
pids=()
trap 'echo "Stopping..."; for p in "${pids[@]:-}"; do kill "$p" 2>/dev/null || true; done' TERM INT EXIT

run_migrations() {
  echo "🚀 Running database migrations ..."
  python -m your_service_name.backend.db.run_migrations || {
    echo "[entrypoint] WARNING: Migration failed; see the error above. The backend starts anyway and answers 503 on /health until the schema matches (the error names an invalid variable)." >&2
  }
}

start_backend() {
  echo "🚀 Starting backend ..."
  # The one launcher (the image runs it too): Uvicorn with the same fixed options, the graceful
  # timeout and the metrics server. It reads these from the environment.
  export BACKEND_ENTRY BACKEND_BIND BACKEND_PORT BACKEND_GRACEFUL_TIMEOUT BACKEND_RELOAD
  python -m your_service_name.backend.serve &
  pids+=("$!")
}

start_frontend_react() {
  echo "🚀 Starting frontend (React / Next.js) ..."
  export NODE_ENV=production
  export NEXT_TELEMETRY_DISABLED=1

  local server_path="${FRONTEND_REACT_SERVER}"
  local app_root; app_root="$(cd "$(dirname "$server_path")/../.." && pwd)"

  echo "[entrypoint] React app root: ${app_root}"
  check_next_runtime "${server_path}"

  # Validate Next.js runtime layout (single invariant across all environments)
  [[ -d "${app_root}/.next/static" ]] || {
    echo "❌ Missing static assets at ${app_root}/.next/static" >&2
    exit 1
  }

  [[ -d "${app_root}/public" ]] || {
    echo "❌ Missing public assets at ${app_root}/public" >&2
    exit 1
  }

  # Self-contain the standalone bundle before launching it. The standalone
  # server.js does `process.chdir(__dirname)` and then serves its client bundles
  # from `<server dir>/.next/static` and public files from `<server dir>/public`.
  # Next bakes those as an ABSOLUTE symlink (.next/standalone/.next/static ->
  # <build host>/.next/static) that resolves on the build host but dangles once the
  # bundle is copied elsewhere — e.g. a CI artifact restored into a different
  # container — and then every /_next/static chunk 404s: the page still renders its
  # SSR shell ("/" is 200, the health wait passes) but never hydrates, so all
  # client-only E2E assertions fail. The Dockerfile is unaffected because COPY
  # dereferences the symlink into real files (Dockerfile.frontend 85-87); replicate
  # that here so the bundle serves its chunks wherever it was built or restored.
  local standalone_dir; standalone_dir="$(cd "$(dirname "$server_path")" && pwd)"
  local bundle_next="${standalone_dir}/.next"
  if _is_unresolved "${bundle_next}/static"; then
    rm -f "${bundle_next}/static"
    cp -aL "${app_root}/.next/static" "${bundle_next}/static"
    echo "[entrypoint] Standalone .next/static unresolved — materialized a real copy." >&2
  fi
  if _is_unresolved "${standalone_dir}/public"; then
    rm -f "${standalone_dir}/public"
    cp -aL "${app_root}/public" "${standalone_dir}/public"
    echo "[entrypoint] Standalone public unresolved — materialized a real copy." >&2
  fi

  # Launch from app root so the relative `.next/standalone/server.js` path resolves;
  # the server then chdir()s into its own dir and serves the (now real) assets.
  (
    cd "$app_root"
    HOSTNAME="${FRONTEND_BIND:-${FRONTEND_HOST}}" PORT="${FRONTEND_PORT}" \
      node .next/standalone/server.js
  ) &
  pids+=("$!")
}

# ---- Dispatcher ------------------------------------------------------
case "${SERVICE_MODE}" in
  backend)
    print_backend_banner "${BACKEND_HOST}" "${BACKEND_PORT}"
    run_migrations
    start_backend
    $NO_WAIT || wait_for_200 "http://$(connect_host "${BACKEND_HOST}"):${BACKEND_PORT}/api/${API_VERSION}/health" "backend"
    ;;

  frontend)
    print_frontend_banner "${FRONTEND_HOST}" "${FRONTEND_PORT}"
    start_frontend_react
    $NO_WAIT || wait_for_200 "http://$(connect_host "${FRONTEND_HOST}"):${FRONTEND_PORT}/" "frontend"
    ;;

  all)
    echo "▶ Starting ALL services: backend + frontend"
    print_backend_banner "${BACKEND_HOST}" "${BACKEND_PORT}"
    print_frontend_banner "${FRONTEND_HOST}" "${FRONTEND_PORT}"

    # Run migrations BEFORE starting backend (avoids SQLite lock contention)
    run_migrations

    # Start backend
    start_backend

    # Start frontend
    start_frontend_react

    # Health checks
    $NO_WAIT || wait_for_200 "http://$(connect_host "${BACKEND_HOST}"):${BACKEND_PORT}/api/${API_VERSION}/health" "backend"
    $NO_WAIT || wait_for_200 "http://$(connect_host "${FRONTEND_HOST}"):${FRONTEND_PORT}/" "frontend"
    ;;
  *)
    echo "❌ Unknown SERVICE_MODE=${SERVICE_MODE}" >&2
    exit 1
    ;;
esac

# ---- Wait & propagate non-zero exits --------------------------------
set +e
while true; do
  wait -n
  code=$?
  if [[ $code -ne 0 ]]; then
    echo "❌ Child process exited with code $code" >&2
    exit $code
  fi
done
