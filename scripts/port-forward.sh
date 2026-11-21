#!/usr/bin/env bash
# your-service-name — adaptable service template (see wiki/getting-started.md)
# ===========================================================================
# port-forward.sh — self-cleaning multi-service port forwarding
# ===========================================================================
# Purpose
#   Open `kubectl port-forward` tunnels to every workload listed in
#   scripts/services.yaml (or a --only subset), so you can reach the in-cluster
#   services on localhost. Every tunnel is launched in the background and killed
#   automatically on exit / Ctrl-C — no orphaned `port-forward` processes left
#   holding a local port after the script is gone.
#
# Why not just `kubectl port-forward` by hand?
#   - One tunnel is easy; two (backend + frontend) means juggling
#     terminals. This starts them all and tears them all down together.
#   - The namespace and the local ports are discovered, not hard-coded: the
#     namespace comes from the chart convention (or services.yaml); the container port
#     comes from services.yaml; you override the *local* port only if you must.
#
# Key behaviors
#   - Self-cleaning: an EXIT/INT/TERM trap kills every tunnel it started.
#   - Waits for each tunnel to accept connections before declaring it up
#     (so a script that runs immediately after this one does not race the pod).
#   - Config-driven: add a row to services.yaml to add a forwardable service.
#   - --dry-run prints the exact `kubectl port-forward` argv and starts nothing.
#
# Inputs (flags)
#   -e, --env ENV          dev|qa|production — namespace from the chart convention
#   -n, --namespace NS     target namespace (default: <release>-<env> / services.yaml)
#       --only svc1,svc2   restrict to these service keys (e.g. backend,frontend)
#       --port key=lp      override the LOCAL port for a service (e.g. backend=9000)
#       --no-wait          do not probe each tunnel for readiness
#       --list             print the planned forwards and exit
#       --dry-run          print each command, start nothing
#   -h, --help
# ===========================================================================
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -Eeuo pipefail
set -o errtrace

# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
trap 'lib_err_trap "port-forward" "${LINENO}" "${BASH_COMMAND}" "$?"' ERR
trap 'echo; log_warn "interrupted — cleaning up forwards"; exit 130' INT TERM

# ---- Defaults ---------------------------------------------------------------
ENV_NAME=""
NAMESPACE=""
ONLY=""
NO_WAIT="false"
DRY_RUN="${DRY_RUN:-false}"
declare -A ONLY_SET=()
declare -A LOCAL_PORT=()     # key -> local port override
declare -a FW_PIDS=()        # PIDs of the port-forward processes we start

# Self-cleaning: kill every tunnel we started, on any exit path.
cleanup_forwards() {
  local n="${#FW_PIDS[@]}" pid
  [[ "${n}" -eq 0 ]] && return 0
  log_info "stopping ${n} port-forward(s)…"
  for pid in "${FW_PIDS[@]}"; do
    kill "${pid}" >/dev/null 2>&1 || true
  done
  wait 2>/dev/null || true
  log_ok "all port-forwards stopped"
}
trap cleanup_forwards EXIT

print_usage() {
  cat <<'EOF'
Usage: port-forward.sh [options]

Options:
  -e, --env ENV         dev|qa|production — namespace from the chart convention
  -n, --namespace NS    target namespace (default: <release>-<env> / services.yaml)
      --only svc1,svc2  restrict to these service keys (e.g. backend,frontend)
      --port key=lp     override the LOCAL port for a service (e.g. backend=9000)
      --no-wait         do not probe each tunnel for readiness
      --list            print the planned forwards and exit
      --dry-run         print each command, start nothing
  -h, --help            show this help

Examples:
  ./scripts/port-forward.sh --env dev                 # all services, ns from the chart convention
  ./scripts/port-forward.sh --only backend            # just the backend
  ./scripts/port-forward.sh --env qa --port backend=9000
  ./scripts/port-forward.sh --list --env dev          # preview, start nothing
EOF
}

# ---- Flags ------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    -e|--env)        ENV_NAME="$2"; shift 2 ;;
    -n|--namespace)  NAMESPACE="$2"; shift 2 ;;
    --only)          ONLY="$2"; shift 2 ;;
    --port)          LOCAL_PORT["${2%%=*}"]="${2#*=}"; shift 2 ;;
    --no-wait)       NO_WAIT="true"; shift ;;
    --list)          ACTION_LIST="true"; shift ;;
    --dry-run)       DRY_RUN="true"; shift ;;
    -h|--help)       print_usage; exit 0 ;;
    *) die "Unknown option: $1 (try --help)" ;;
  esac
done

# ---- Resolve namespace (override > chart convention > services.yaml) ---------
resolve_namespace() {
  [ -n "${NAMESPACE}" ] && return 0
  if [ -n "${ENV_NAME}" ]; then
    NAMESPACE="$(lib_helm_ns app "${ENV_NAME}")"
    [ -n "${NAMESPACE}" ] || die "could not resolve namespace for env '${ENV_NAME}'"
    return 0
  fi
  # Fall back to the services.yaml namespace (pure structure, no env values).
  need_cmd python3
  NAMESPACE="$(python3 - "${LIB_SERVICES_YAML}" <<'PY'
import sys, yaml
print((yaml.safe_load(open(sys.argv[1])) or {}).get("namespace", "") or "")
PY
)"
  [ -n "${NAMESPACE}" ] || die "no namespace: pass --namespace or --env"
}

# ---- Preflight --------------------------------------------------------------
need_cmd kubectl
[ "${DRY_RUN}" = "true" ] || require_kube
resolve_namespace

# ---- Load services (key / workload / port) from services.yaml -----------------
declare -a SVC_KEYS=() SVC_WORKLOADS=() SVC_PORTS=() SVC_SCHEMES=()
load_services() {
  need_cmd python3
  local rec k wl pt sc
  mapfile -t SVC_RECORDS < <(python3 - "${LIB_SERVICES_YAML}" <<'PY'
import sys, yaml
data = yaml.safe_load(open(sys.argv[1])) or {}
for s in (data.get("services") or []):
    print("\t".join([str(s.get("key", "")), str(s.get("workload", "")), str(s.get("port", "")), str(s.get("scheme", ""))]))
PY
)
  for rec in "${SVC_RECORDS[@]}"; do
    IFS=$'\t' read -r k wl pt sc <<< "${rec}"
    [ -n "${k}" ] || continue
    SVC_KEYS+=("${k}"); SVC_WORKLOADS+=("${wl}"); SVC_PORTS+=("${pt}"); SVC_SCHEMES+=("${sc}")
  done
  [ "${#SVC_KEYS[@]}" -gt 0 ] || die "no services defined in ${LIB_SERVICES_YAML}"
}
load_services

# Validate every service's forward scheme once, up front, so both --list and the
# live run fail identically on a bad value (an empty entry defaults to http).
# This mirrors build_probe's probe_tool check in rebuild-and-roll — a config
# typo should abort loudly rather than emit a dead link that looks live.
for i in "${!SVC_KEYS[@]}"; do
  sc="${SVC_SCHEMES[$i]:-http}"
  case "${sc}" in
    http|https) ;;
    *) die "unknown scheme '${sc}' for [${SVC_KEYS[$i]}] (use http | https)" ;;
  esac
done

# ---- --only filter ----------------------------------------------------------
if [ -n "${ONLY}" ]; then
  IFS=',' read -ra _only_arr <<< "${ONLY}"
  for s in "${_only_arr[@]}"; do ONLY_SET["${s// /}"]="1"; done
fi

# ---- One forward ------------------------------------------------------------
# Waits until the local port accepts a TCP connection (unless --no-wait), so
# callers can rely on the tunnel being live the moment this returns.
wait_port_open() {                    # $1 local-port  $2 label
  local lp="$1" label="$2" i
  for ((i = 1; i <= 20; i++)); do
    if (exec 3<>/dev/tcp/127.0.0.1/"${lp}") >/dev/null 2>&1; then
      exec 3>&- 3<&- 2>/dev/null || true
      return 0
    fi
    sleep 0.25
  done
  log_warn "[${label}] tunnel on 127.0.0.1:${lp} not accepting connections yet"
  return 1
}

start_forward() {                     # $1 key  $2 workload  $3 container-port  $4 scheme
  # Scheme comes in as $4 (the dispatch loop resolves it by the numeric index
  # $i); do NOT index SVC_SCHEMES by the string key $1 here — SVC_SCHEMES is a
  # numeric-indexed array, so a string subscript is evaluated arithmetically and
  # under `set -u` dies with "<key>: unbound variable" (that was the bug).
  local key="$1" wl="$2" cport="$3" scheme="${4:-http}" \
        lport="${LOCAL_PORT[$1]:-${3}}"
  [ -n "${wl}" ] || { log_warn "[${key}] has no workload — skipping"; return 0; }
  log_step "[${key}] ${wl}: ${scheme}://127.0.0.1:${lport} -> ${wl}:${cport} (ns: ${NAMESPACE})"
  if [ "${DRY_RUN}" = "true" ]; then
    printf '%s%s⇢ %s%s\n' "$(_lib_ts)" "${C_YEL}" \
      "kubectl port-forward -n ${NAMESPACE} deployment/${wl} ${lport}:${cport}" "${C_RST}"
    return 0
  fi
  kubectl port-forward -n "${NAMESPACE}" "deployment/${wl}" "${lport}:${cport}" \
    >/dev/null 2>&1 &
  local pid=$!
  FW_PIDS+=("${pid}")
  log_ok "[${key}] forwarding (pid ${pid})"
  if [ "${NO_WAIT}" != "true" ]; then
    if wait_port_open "${lport}" "${key}"; then
      # Emit the URL with its scheme: terminal emulators only auto-link a string
      # that carries a scheme, so a bare host:port is not clickable. http (the
      # default) is correct for the shipped services — the pods serve plaintext
      # HTTP and TLS terminates at the ingress, so a direct forward is plain.
      log_meta "[${key}] ${scheme}://127.0.0.1:${lport} is open"
    fi
  fi
}

# ---- Dispatch: --list vs. run ----------------------------------------------
if [ "${ACTION_LIST:-false}" = "true" ] || [ "${DRY_RUN}" = "true" ]; then
  lib_banner "port-forward (preview)" "env=${ENV_NAME:-<none>}  ns=${NAMESPACE}"
  for i in "${!SVC_KEYS[@]}"; do
    key="${SVC_KEYS[$i]}"; wl="${SVC_WORKLOADS[$i]}"; cport="${SVC_PORTS[$i]}" sc="${SVC_SCHEMES[$i]:-http}"
    if [ "${#ONLY_SET[@]}" -gt 0 ] && [ -z "${ONLY_SET[$key]:-}" ]; then continue; fi
    lport="${LOCAL_PORT[$key]:-${cport}}"
    log_info "[${key}] ${sc}://127.0.0.1:${lport} -> deployment/${wl}:${cport}"
  done
  [ "${DRY_RUN}" = "true" ] && log_warn "dry-run: nothing was started"
  exit 0
fi

lib_banner "port-forward" "ns=${NAMESPACE}  (Ctrl-C or exit stops all forwards)"
for i in "${!SVC_KEYS[@]}"; do
  key="${SVC_KEYS[$i]}"; wl="${SVC_WORKLOADS[$i]}"; cport="${SVC_PORTS[$i]}" sc="${SVC_SCHEMES[$i]:-http}"
  if [ "${#ONLY_SET[@]}" -gt 0 ] && [ -z "${ONLY_SET[$key]:-}" ]; then
    log_info "skip ${key} (not in --only)"; continue
  fi
  start_forward "${key}" "${wl}" "${cport}" "${sc}"
done

if [ "${#FW_PIDS[@]}" -eq 0 ]; then
  die "no services to forward (check --only / services.yaml)"
fi

log_ok "forwards are live — press Ctrl-C to stop"
wait || true   # block until a signal tears everything down via the EXIT trap
