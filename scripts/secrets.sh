#!/usr/bin/env bash
# your-service-name — adaptable service template (see wiki/getting-started.md)
# ===========================================================================
# secrets.sh — create / update / inspect a Kubernetes Secret, safely
# ===========================================================================
# Purpose
#   Turn a local .env (or --key VALUE pairs) into a `generic` Secret in the
#   resolved namespace — create-or-update, idempotent — and inspect an existing
#   Secret's KEYS without ever printing a value.
#
# Secret-safety rules enforced here:
#   - Values are parsed with _lib.sh's safe loader (no `source`, no code exec).
#   - Values are NEVER printed — not in logs, not in --dry-run output (masked).
#   - Refuses to run as root (secrets on disk + root = bad idea).
#   - `show` lists only key names, each value shown as a mask.
#
# Decoupled from the deploy flow
#   A standard deploy (k8s-env.sh → helm upgrade --install) now takes its secrets
#   from values.<env>.yaml — or, in real clusters, sealed-secrets / ESO / SOPS — so
#   nothing in the deploy path calls this script any more. secrets.sh stays as a
#   general, secret-safe tool for creating / inspecting / rotating a generic Secret
#   by hand: e.g. a one-off credential that lives outside the chart's values.
#
# Actions
#   update   create-or-update the Secret from a .env file or --key pairs
#   show     list the Secret's keys (values masked) — reads live cluster state
#   delete   delete the Secret (asks to confirm)
#
# Inputs (flags)
#   -e, --env ENV          dev|qa|production — namespace from the chart convention
#   -n, --namespace NS     target namespace (default: <release>-<env> / services.yaml)
#   -s, --secret NAME      secret name (default: your-service-name-secret)
#   -f, --from FILE        .env file to read KEY=VALUE from (update)
#       --key K=V          add a literal KEY=VALUE (repeatable; update)
#       --required K1,K2   keys that MUST be present or we refuse to write
#       --dry-run          print the masked command, write nothing
#   -y, --yes              assume yes (skip the delete confirmation)
#   -h, --help
# ===========================================================================
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -Eeuo pipefail
set -o errtrace

# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
trap 'lib_err_trap "secrets" "${LINENO}" "${BASH_COMMAND}" "$?"' ERR
trap 'echo; log_err "interrupted — aborting"; exit 130' INT TERM

# ---- Defaults ---------------------------------------------------------------
ACTION="update"
ENV_NAME=""
NAMESPACE=""
SECRET_NAME="your-service-name-secret"
ENV_FILE=""
REQUIRED=""
declare -a KEY_ARGS=()       # literal "K=V" strings from --key
declare -A ENVV=()           # loaded secrets (associative: key -> value)

print_usage() {
  cat <<'EOF'
Usage: secrets.sh [action] [options]

Actions (default: update):
  update   create-or-update the Secret from a .env file or --key pairs
  show     list the Secret's keys (values masked)
  delete   delete the Secret (asks to confirm)

Options:
  -e, --env ENV         dev|qa|production — namespace from the chart convention
  -n, --namespace NS    target namespace (default: <release>-<env> / services.yaml)
  -s, --secret NAME     secret name (default: your-service-name-secret)
  -f, --from FILE       .env file to read KEY=VALUE from (update)
      --key K=V         add a literal KEY=VALUE (repeatable; update)
      --required K1,K2  keys that MUST be present or we refuse to write
      --dry-run         print the masked command, write nothing
  -y, --yes             assume yes (skip the delete confirmation)
  -h, --help            show this help

Examples:
  ./scripts/secrets.sh update --env dev -f .env
  ./scripts/secrets.sh update --env qa --key DB_PASSWORD=hunter2 --key DB_USER=app
  ./scripts/secrets.sh show --env dev
  ./scripts/secrets.sh delete --env dev --yes
  ./scripts/secrets.sh update --env dev -f .env --required DB_PASSWORD,DB_USER --dry-run
EOF
}

# ---- Flags ------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    update|show|delete)  ACTION="$1"; shift ;;
    -e|--env)        ENV_NAME="$2"; shift 2 ;;
    -n|--namespace)  NAMESPACE="$2"; shift 2 ;;
    -s|--secret)     SECRET_NAME="$2"; shift 2 ;;
    -f|--from)       ENV_FILE="$2"; shift 2 ;;
    --key)           KEY_ARGS+=("$2"); shift 2 ;;
    --required)      REQUIRED="$2"; shift 2 ;;
    --dry-run)       DRY_RUN="true"; shift ;;
    -y|--yes)
      # shellcheck disable=SC2034  # consumed by confirm()/confirm_break_glass in the sourced _lib.sh
      LIB_ASSUME_YES="true"
      shift
      ;;
    -h|--help)       print_usage; exit 0 ;;
    *) die "Unknown option: $1 (try --help)" ;;
  esac
done

assert_non_root

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

# ---- Gather key/value pairs into the ENVV assoc array (never printed whole) ---
gather_secrets() {
  local have_file="false" have_keys="false"
  # 1. --key pairs (highest precedence — let a CLI override win)
  if [ "${#KEY_ARGS[@]}" -gt 0 ]; then
    local kv k v
    for kv in "${KEY_ARGS[@]}"; do
      [[ "${kv}" == *=* ]] || die "--key expects KEY=VALUE, got: ${kv}"
      k="${kv%%=*}"; v="${kv#*=}"
      [[ "${k}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || die "invalid key in --key: ${k}"
      ENVV["${k}"]="${v}"
      have_keys="true"
    done
  fi
  # 2. --from file
  if [ -n "${ENV_FILE}" ]; then
    [ -f "${ENV_FILE}" ] || die "env file not found: ${ENV_FILE}"
    load_env_file "${ENV_FILE}" ENVV
    have_file="true"
  fi
  # If neither was given, fall back to a conventional .env beside the repo root.
  if [ "${have_file}" = "false" ] && [ "${have_keys}" = "false" ]; then
    for cand in "${LIB_REPO_ROOT}/.env" "${LIB_REPO_ROOT}/.env.example"; do
      if [ -f "${cand}" ]; then ENV_FILE="${cand}"; load_env_file "${cand}" ENVV; have_file="true"; break; fi
    done
  fi
  [ "${have_file}" = "true" ] || [ "${have_keys}" = "true" ] \
    || die "no secrets supplied: pass -f FILE and/or --key K=V"
}

# ---- Required-key check -----------------------------------------------------
check_required() {
  [ -n "${REQUIRED}" ] || return 0
  local k missing=()
  IFS=',' read -ra _req <<< "${REQUIRED}"
  for k in "${_req[@]}"; do
    k="${k// /}"
    [ -n "${ENVV[$k]:-}" ] || missing+=("${k}")
  done
  [ "${#missing[@]}" -eq 0 ] || die "missing required secret key(s): ${missing[*]}"
}

# ---- Masked display of the keys we are about to write ------------------------
log_secret_keys() {                     # prints "KEY -> su****" per key
  local k
  for k in "${!ENVV[@]}"; do
    log_meta "  ${k} -> $(mask "${ENVV[$k]}")"
  done
}

# ---- Actions ----------------------------------------------------------------
cmd_update() {
  gather_secrets
  check_required
  lib_banner "secrets update" "ns=${NAMESPACE}  secret=${SECRET_NAME}  keys=${#ENVV[@]}"
  log_info "the following keys will be written (values masked):"
  log_secret_keys

  # Build --from-literal args (real values) for the actual command.
  local -a literals=() masked=()
  local k
  for k in "${!ENVV[@]}"; do
    literals+=("--from-literal=${k}=${ENVV[$k]}")
    masked+=("--from-literal=${k}=$(mask "${ENVV[$k]}")")
  done

  # Create-or-update: render with --dry-run=client, then apply. Idempotent.
  if [ "${DRY_RUN}" = "true" ]; then
    log_step "kubectl create secret generic ${SECRET_NAME} -n ${NAMESPACE} \\"
    local m
    for m in "${masked[@]}"; do log_step "    ${m} \\"; done
    log_step "    --dry-run=client -o yaml | kubectl apply -f -"
    log_warn "dry-run: nothing was written"
    return 0
  fi

  log_step "writing secret ${SECRET_NAME} (${#ENVV[@]} keys) to ${NAMESPACE}"
  if kubectl create secret generic "${SECRET_NAME}" -n "${NAMESPACE}" \
        "${literals[@]}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null; then
    log_ok "secret ${SECRET_NAME} updated (${#ENVV[@]} keys)"
  else
    die "failed to write secret ${SECRET_NAME}"
  fi
}

cmd_show() {
  lib_banner "secrets show" "ns=${NAMESPACE}  secret=${SECRET_NAME}"
  log_step "kubectl -n ${NAMESPACE} get secret ${SECRET_NAME}"
  # Keys are not sensitive; values are. Print keys + a mask only.
  local raw keys
  if ! raw="$(kubectl -n "${NAMESPACE}" get secret "${SECRET_NAME}" -o jsonpath='{.data}' 2>/dev/null)"; then
    die "secret ${SECRET_NAME} not found in ${NAMESPACE} (create it with: secrets.sh update)"
  fi
  keys="$(printf '%s' "${raw}" \
            | python3 -c 'import sys,json;print(" ".join(json.load(sys.stdin).keys()))' 2>/dev/null || true)"
  if [ -z "${keys}" ]; then
    die "secret ${SECRET_NAME} has no keys (or could not be parsed)"
  fi
  log_ok "keys present in ${SECRET_NAME}:"
  local key
  for key in ${keys}; do
    log_meta "  - ${key} -> $(mask "x")"
  done
}

cmd_delete() {
  lib_banner "secrets delete" "ns=${NAMESPACE}  secret=${SECRET_NAME}"
  confirm "delete secret ${SECRET_NAME} from ${NAMESPACE}?" || exit 0
  log_step "kubectl delete secret ${SECRET_NAME} -n ${NAMESPACE}"
  run kubectl delete secret "${SECRET_NAME}" -n "${NAMESPACE}"
  log_ok "secret ${SECRET_NAME} deleted"
}

# ---- Dispatch ---------------------------------------------------------------
resolve_namespace
case "${ACTION}" in
  update) cmd_update ;;
  show)   cmd_show ;;
  delete) cmd_delete ;;
  *)      die "unknown action: ${ACTION} (use update | show | delete)" ;;
esac
