#!/usr/bin/env bash
# your-service-name — adaptable service template (see wiki/getting-started.md)
# ===========================================================================
# logs.sh — aggregate logs across every service in one stream
# ===========================================================================
# Purpose
#   Pull `kubectl logs` for all workloads in scripts/services.yaml (or a --only
#   subset) into a single, per-line-labelled view — so you can watch backend +
#   frontend together instead of tabbing between two `logs -f`.
#
# Modes
#   snapshot (default)  print the last --tail lines of each service, in turn
#   --follow            tail -f all services concurrently; Ctrl-C stops all
#
# Each line is prefixed with the service key (--prefix), so the interleaved
# follow stream stays attributable. Config-driven: a row in services.yaml is a
# log source.
#
# Inputs (flags)
#   -e, --env ENV          dev|qa|production — namespace from the chart convention
#   -n, --namespace NS     target namespace (default: <release>-<env> / services.yaml)
#       --only svc1,svc2   restrict to these service keys (e.g. backend,frontend)
#   -f, --follow           tail -f all services concurrently (self-cleaning)
#   -t, --tail N           lines per container (default: 100; 0 = all)
#       --since DUR        only logs newer than DUR (e.g. 30m, 2h)
#   -c, --container        show only this container name (optional)
#       --dry-run          print each kubectl command, run nothing
#   -h, --help
# ===========================================================================
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -Eeuo pipefail
set -o errtrace

# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
trap 'lib_err_trap "logs" "${LINENO}" "${BASH_COMMAND}" "$?"' ERR
trap 'echo; log_warn "interrupted — stopping log streams"; exit 130' INT TERM

# ---- Defaults ---------------------------------------------------------------
ENV_NAME=""
NAMESPACE=""
FOLLOW="false"
TAIL="100"
SINCE=""
CONTAINER=""
DRY_RUN="${DRY_RUN:-false}"
declare -A ONLY_SET=()
declare -a LOG_PIDS=()

print_usage() {
  cat <<'EOF'
Usage: logs.sh [options]

Options:
  -e, --env ENV         dev|qa|production — namespace from the chart convention
  -n, --namespace NS    target namespace (default: <release>-<env> / services.yaml)
      --only svc1,svc2  restrict to these service keys (e.g. backend,frontend)
  -f, --follow          tail -f all services concurrently (self-cleaning)
  -t, --tail N          lines per container (default: 100; 0 = all)
      --since DUR       only logs newer than DUR (e.g. 30m, 2h)
  -c, --container       show only this container name (optional)
      --dry-run         print each kubectl command, run nothing
  -h, --help            show this help

Examples:
  ./scripts/logs.sh --env dev                       # last 100 lines, all services
  ./scripts/logs.sh --env dev --only backend --tail 300
  ./scripts/logs.sh -f --env qa                     # live, interleaved, Ctrl-C to stop
  ./scripts/logs.sh -f --env dev --since 5m
EOF
}

# ---- Flags ------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    -e|--env)        ENV_NAME="$2"; shift 2 ;;
    -n|--namespace)  NAMESPACE="$2"; shift 2 ;;
    --only)          IFS=',' read -ra _only_arr <<< "$2"; for s in "${_only_arr[@]}"; do ONLY_SET["${s// /}"]="1"; done; shift 2 ;;
    -f|--follow)     FOLLOW="true"; shift ;;
    -t|--tail)       TAIL="$2"; shift 2 ;;
    --since)         SINCE="$2"; shift 2 ;;
    -c|--container)  CONTAINER="$2"; shift 2 ;;
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
  need_cmd python3
  NAMESPACE="$(python3 - "${LIB_SERVICES_YAML}" <<'PY'
import sys, yaml
print((yaml.safe_load(open(sys.argv[1])) or {}).get("namespace", "") or "")
PY
)"
  [ -n "${NAMESPACE}" ] || die "no namespace: pass --namespace or --env"
}

# ---- Load services (key / workload) -----------------------------------------
declare -a SVC_KEYS=() SVC_WORKLOADS=()
load_services() {
  need_cmd python3
  local rec k wl
  mapfile -t SVC_RECORDS < <(python3 - "${LIB_SERVICES_YAML}" <<'PY'
import sys, yaml
data = yaml.safe_load(open(sys.argv[1])) or {}
for s in (data.get("services") or []):
    print("\t".join([str(s.get("key", "")), str(s.get("workload", ""))]))
PY
)
  for rec in "${SVC_RECORDS[@]}"; do
    IFS=$'\t' read -r k wl <<< "${rec}"
    [ -n "${k}" ] || continue
    SVC_KEYS+=("${k}"); SVC_WORKLOADS+=("${wl}")
  done
  [ "${#SVC_KEYS[@]}" -gt 0 ] || die "no services defined in ${LIB_SERVICES_YAML}"
}
load_services

# ---- Self-cleaning follow ---------------------------------------------------
cleanup_logs() {
  local n="${#LOG_PIDS[@]}" pid
  [[ "${n}" -eq 0 ]] && return 0
  for pid in "${LOG_PIDS[@]}"; do kill "${pid}" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap cleanup_logs EXIT

# ---- One service's logs -----------------------------------------------------
logs_argv() {                        # echoes the kubectl logs argv for a workload
  local wl="$1"
  set -- kubectl logs -n "${NAMESPACE}" "deployment/${wl}" --prefix
  [ -n "${CONTAINER}" ] && set -- "$@" -c "${CONTAINER}"
  [ "${TAIL}" != "0" ] && set -- "$@" --tail="${TAIL}"
  [ -n "${SINCE}" ] && set -- "$@" --since="${SINCE}"
  [ "${FOLLOW}" = "true" ] && set -- "$@" -f
  printf '%s\n' "$*"
}

# ---- Dispatch: snapshot vs follow ------------------------------------------
resolve_namespace
need_cmd kubectl
[ "${DRY_RUN}" = "true" ] || require_kube

MODE="snapshot"; [ "${FOLLOW}" = "true" ] && MODE="follow"
lib_banner "logs" "ns=${NAMESPACE}  mode=${MODE}  tail=${TAIL}${SINCE:+  since=${SINCE}}"

n_targeted=0
for i in "${!SVC_KEYS[@]}"; do
  key="${SVC_KEYS[$i]}"; wl="${SVC_WORKLOADS[$i]}"
  if [ "${#ONLY_SET[@]}" -gt 0 ] && [ -z "${ONLY_SET[$key]:-}" ]; then
    log_info "skip ${key} (not in --only)"; continue
  fi
  n_targeted=$((n_targeted + 1))
  argv="$(logs_argv "${wl}")"
  if [ "${DRY_RUN}" = "true" ]; then
    printf '%s%s⇢ %s%s\n' "$(_lib_ts)" "${C_YEL}" "${argv}" "${C_RST}"
    continue
  fi
  if [ "${FOLLOW}" = "true" ]; then
    # Background each stream; the EXIT trap stops them all together.
    ( exec ${argv} ) &
    LOG_PIDS+=("$!")
  else
    lib_sep "▶ ${key} (${wl})"
    # shellcheck disable=SC2086
    eval ${argv} || log_warn "[${key}] could not read logs (pod may not be running)"
  fi
done

[ "${n_targeted}" -gt 0 ] || die "no services selected (check --only / services.yaml)"

if [ "${FOLLOW}" = "true" ] && [ "${DRY_RUN}" != "true" ]; then
  [ "${#LOG_PIDS[@]}" -gt 0 ] && log_ok "following ${#LOG_PIDS[@]} streams — Ctrl-C to stop"
  wait || true
fi
