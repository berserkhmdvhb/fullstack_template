#!/usr/bin/env bash
# =============================================================================
# _lib.sh — Shared runtime for the scripts/ operations toolkit
# =============================================================================
# Sourced (never executed) by every script in this directory. It provides one
# consistent surface for the things every operations script needs:
#
#   - Colour + emoji logging that degrades gracefully (no TTY / NO_COLOR / dumb)
#   - Framed banners and section dividers (the "UI/UX" of a CLI)
#   - Dry-run-aware command runners (run / run_sh)
#   - A *safe* .env parser (no `source` — arbitrary code is never executed)
#   - Secret masking, interactive confirmation, pre-flight command/cluster checks
#   - Namespace discovery from a built Kustomize overlay
#
# Design rules baked in here (and expected of every script that uses it):
#   - set -Eeuo pipefail + errtrace + an ERR trap that names the failing line
#   - every state-mutating external command goes through run()/run_sh()
#   - secrets are loaded into an associative array and are never printed whole
#   - destructive actions require --yes/-y or an interactive confirmation
#
# Usage in a script:
#   #!/usr/bin/env bash
#   set -Eeuo pipefail; set -o errtrace
#   source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
#   trap 'lib_err_trap "${BASH_SOURCE[0]##*}"' ERR
# =============================================================================

# Guard against double-sourcing (keeps re-source idempotent).
if [[ -n "${_LIB_SOURCED:-}" ]]; then
  # shellcheck disable=SC2317  # reachable on re-source (this is the idempotent-sourcing guard)
  return 0 2>/dev/null || true
fi
_LIB_SOURCED=1

# ---------------------------------------------------------------------------
# Colour resolution — honour NO_COLOR, a non-TTY stdout, and `dumb` terminals.
# ---------------------------------------------------------------------------
if [[ -n "${NO_COLOR:-}" || ! -t 1 || "${TERM:-dumb}" == "dumb" ]]; then
  _LIB_COLOR=0
else
  _LIB_COLOR=1
fi

if [[ "${_LIB_COLOR}" -eq 1 ]]; then
  C_RED="$(printf '\033[31m')";   C_GREEN="$(printf '\033[32m')"
  C_YEL="$(printf '\033[33m')";   C_BLU="$(printf '\033[34m')"
  C_MAG="$(printf '\033[35m')";   C_CYN="$(printf '\033[36m')"
  C_DIM="$(printf '\033[2m')";    C_BLD="$(printf '\033[1m')"
  C_RST="$(printf '\033[0m')"
else
  C_RED=""; C_GREEN=""; C_YEL=""; C_BLU=""; C_MAG=""; C_CYN=""; C_DIM=""; C_BLD=""; C_RST=""
fi

# Timestamps on log lines (compact HH:MM:SS). Disable with LIB_TS=0.
LIB_TS="${LIB_TS:-1}"

# ---------------------------------------------------------------------------
# Small primitives
# ---------------------------------------------------------------------------
lib_now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
lib_clock() { date +%H:%M:%S; }

_lib_ts() {
  [[ "${LIB_TS}" == "0" ]] && return 0
  printf '%s%s%s ' "${C_DIM}" "$(lib_clock)" "${C_RST}"
}

# ---------------------------------------------------------------------------
# Logging — emoji-prefixed, timestamped. Errors/warnings go to stderr so a
# consumer can pipe stdout and still see problems.
# ---------------------------------------------------------------------------
log_step()  { printf '%s%s▶  %s%s\n'  "$(_lib_ts)" "${C_BLD}${C_CYN}" "$*" "${C_RST}"; }
log_info()  { printf '%s%sℹ  %s%s\n'  "$(_lib_ts)" "${C_BLU}" "$*" "${C_RST}"; }
log_meta()  { printf '%s%s▸  %s%s\n'  "$(_lib_ts)" "${C_DIM}" "$*" "${C_RST}"; }
log_ok()    { printf '%s%s✅ %s%s\n'  "$(_lib_ts)" "${C_GREEN}" "$*" "${C_RST}"; }
log_warn()  { printf '%s%s⚠  %s%s\n' "$(_lib_ts)" "${C_YEL}" "$*" "${C_RST}" >&2; }
log_err()   { printf '%s%s❌ %s%s\n' "$(_lib_ts)" "${C_RED}" "$*" "${C_RST}" >&2; }

die() { log_err "$*"; exit "${LIB_EXIT:-1}"; }

# ERR-trap reporter: names the script, line, and failing command.
lib_err_trap() {
  local script="${1:-<script>}" line="${2:-?}" cmd="${3:-<unknown>}" rc="${4:-$?}"
  log_err "failed at ${script}:${line} (exit ${rc}): ${cmd}"
}

# ---------------------------------------------------------------------------
# Banners & dividers — the visible face of the tool. A banner is printed once at
# the top; dividers separate phases. Width is fixed so output stays aligned in
# CI logs.
# ---------------------------------------------------------------------------
_LIB_WIDTH=60
lib_banner() {
  local title="$1" subtitle="${2:-}"
  local bar; bar="$(printf '━%.0s' $(seq 1 "${_LIB_WIDTH}"))"
  printf '%s%s┏━ %s ' "${C_BLD}${C_MAG}" "${C_RST}" "${C_BLD}${title}${C_RST}"
  printf '%s%s\n' "${bar}" "${C_RST}"
  if [[ -n "${subtitle}" ]]; then
    printf '%s┃ %s%s\n' "${C_DIM}" "${subtitle}" "${C_RST}"
    printf '%s┗%s %s%s\n' "${C_MAG}" "${bar}" "$(lib_now)" "${C_RST}"
  else
    printf '%s┗%s%s\n' "${C_MAG}" "${bar}" "${C_RST}"
  fi
  echo
}

lib_sep() {
  local label="$1"
  local bar; bar="$(printf '━%.0s' $(seq 1 "$((_LIB_WIDTH - ${#label} - 4))"))"
  printf '%s━━━ %s %s%s\n' "${C_CYN}" "${label}" "${bar}" "${C_RST}"
}

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
need_cmd() {
  local c
  local missing=()
  for c in "$@"; do
    command -v "$c" >/dev/null 2>&1 || missing+=("$c")
  done
  [[ ${#missing[@]} -eq 0 ]] || die "missing required command(s): ${missing[*]}"
}

# require_kube — kubectl present AND a cluster reachable.
require_kube() {
  need_cmd kubectl
  if ! kubectl cluster-info >/dev/null 2>&1; then
    die "no reachable Kubernetes cluster (kubectl cluster-info failed). Are credentials/context set?"
  fi
}

# assert_non_root — docker/kubectl ops misbehave as root; refuse loudly.
assert_non_root() {
  [[ "$(id -u)" -ne 0 ]] || die "refusing to run as root (drop privileges or use a user account)"
}

# ---------------------------------------------------------------------------
# Dry-run-aware command runners. Every mutating command is wrapped so
# `--dry-run` prints the exact argv/snippet and executes nothing.
# ---------------------------------------------------------------------------
run() {
  if [[ "${DRY_RUN:-false}" == "true" ]]; then
    printf '%s%s⇢ %s%s\n' "$(_lib_ts)" "${C_YEL}" "$*" "${C_RST}"
    return 0
  fi
  "$@"
}

run_sh() {
  if [[ "${DRY_RUN:-false}" == "true" ]]; then
    printf '%s%s⇢ %s%s\n' "$(_lib_ts)" "${C_YEL}" "$*" "${C_RST}"
    return 0
  fi
  bash -c "$*"
}

# ---------------------------------------------------------------------------
# Interactive confirmation for destructive actions. Bypassed by --yes/-y
# (LIB_ASSUME_YES=true). Always returns 0 under --dry-run (nothing happens).
# ---------------------------------------------------------------------------
LIB_ASSUME_YES="${LIB_ASSUME_YES:-false}"
confirm() {
  local prompt="$1"
  if [[ "${DRY_RUN:-false}" == "true" ]]; then
    printf '%s%s⇢ (dry-run) would confirm: %s%s\n' "$(_lib_ts)" "${C_YEL}" "${prompt}" "${C_RST}"
    return 0
  fi
  if [[ "${LIB_ASSUME_YES}" == "true" ]]; then
    return 0
  fi
  local answer=""
  printf '%s⚠  %s [y/N]%s ' "${C_YEL}" "${prompt}" "${C_RST}" >&2
  read -r -e answer <&2 || answer="n"
  case "${answer,,}" in
    y|yes) return 0 ;;
    *)     log_warn "aborted by user"; return 1 ;;
  esac
}

# ---------------------------------------------------------------------------
# Secret-safe .env loader.
#
# Parses KEY=VALUE (optionally `export KEY=VALUE`), skipping blanks and comments.
# Does NOT `source` the file — arbitrary shell is never executed. Values may be
# single/double quoted; a trailing inline comment is NOT stripped (env values can
# legitimately contain '#'), matching docker-compose .env semantics.
#
# Populates the caller's associative array named by $2 (default ENVV), so scripts
# export only the keys they actually need instead of leaking the whole file.
# ---------------------------------------------------------------------------
load_env_file() {
  local file="$1" arrname="${2:-ENVV}"
  [[ -f "${file}" ]] || die "env file not found: ${file}"

  local lineno=0 line key val
  while IFS= read -r line || [[ -n "${line}" ]]; do
    lineno=$((lineno + 1))
    line="${line%$'\r'}"                                  # tolerate CRLF
    [[ -z "${line// /}" ]] && continue                    # blank
    [[ "${line}" == \#* ]] && continue                    # full-line comment
    line="${line#export }"                                # optional export
    [[ "${line}" == *=* ]] || { log_warn "skipping malformed line ${file}:${lineno}"; continue; }
    key="${line%%=*}"; val="${line#*=}"
    key="${key// /}"
    [[ "${key}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || { log_warn "skipping invalid key ${file}:${lineno}: ${key}"; continue; }
    # Strip one layer of matching surrounding quotes.
    if [[ "${val}" == \"*\" ]]; then val="${val:1:${#val}-2}"
    elif [[ "${val}" == \'*\' ]]; then val="${val:1:${#val}-2}"; fi
    printf -v "${arrname}[$key]" '%s' "${val}"
  done < "${file}"
}

# env_get KEY — read a loaded variable from ENVV (empty if unset).
env_get() {
  local key="$1"
  printf '%s' "${ENVV[$key]:-}"
}

require_env_keys() {
  local arrname="${2:-ENVV}" key missing=()
  for key in "$@"; do
    [[ "${key}" == "$arrname" ]] && continue
    [[ -n "${ENVV[$key]:-}" ]] || missing+=("$key")
  done
  [[ ${#missing[@]} -eq 0 ]] || die "env file is missing required key(s): ${missing[*]}"
}

# ---------------------------------------------------------------------------
# Secret masking — show that a value exists without revealing it.
#   mask "supersecret"        ->  su****
#   mask_conn "postgresql://u:p@h/db" -> postgresql://u:****@h/db
# ---------------------------------------------------------------------------
mask() {
  local s="$1"
  [[ -z "${s}" ]] && { printf '****'; return; }
  if [[ ${#s} -le 4 ]]; then printf '****'; else printf '%s****' "${s:0:2}"; fi
}

mask_conn() {
  # Redact the password component of a URL/conn-string, if present.
  local s="$1"
  printf '%s' "${s}" | sed -E 's#(://[^:/@]+:)[^@]*(@)#\1****\2#'
}


# ---------------------------------------------------------------------------
# Helm namespace/name resolution. The operational surface now drives the chart
# (charts/your-service-name) with `helm upgrade --install`, so there is no
# overlay to render; namespaces follow a deterministic convention instead:
#
#   HELM_RELEASE (default: your-service-name) is the release name. It is chosen
#     to equal the chart name, so chart.fullname collapses to it and every
#     rendered resource keeps its retired-Kustomize name (your-service-name-backend,
#     -secret, -db-credentials, -migration, …) — the e2e checks and
#     services.yaml need no name changes.
#   app namespace = <HELM_RELEASE>-<env>      (e.g. your-service-name-dev)
#   db  namespace = <HELM_RELEASE>-<env>-db   (e.g. your-service-name-dev-db)
#
# The chart derives the db namespace as <.Release.Namespace>-db (namespaces.dbSuffix),
# so these match exactly the namespaces scripts/k8s-bootstrap.sh creates for <env>.
#   lib_helm_ns app dev   ->   your-service-name-dev
#   lib_helm_ns db  dev   ->   your-service-name-dev-db
# ---------------------------------------------------------------------------
lib_helm_ns() {
  local role="${1:-}" env="${2:-}"
  [ -n "${env}" ] || die "lib_helm_ns: environment required (call: lib_helm_ns app|db <env>)"
  local base="${HELM_RELEASE:-your-service-name}-${env}"
  case "${role}" in
    app) printf '%s\n' "${base}" ;;
    db)  printf '%s-db\n' "${base}" ;;
    *)   die "lib_helm_ns: role must be 'app' or 'db', got '${role:-}'" ;;
  esac
}

# lib_helm_values_file ENV — path to the chart's per-env values file (used by
# `helm upgrade --install -f …` and to read an env's image tag/registry).
lib_helm_values_file() {
  local env="${1:-}" f
  [ -n "${env}" ] || die "lib_helm_values_file: environment required"
  f="${LIB_REPO_ROOT}/charts/your-service-name/values.${env}.yaml"
  [ -f "${f}" ] || die "values file not found: ${f} (env '${env}')"
  printf '%s\n' "${f}"
}

# ---------------------------------------------------------------------------
# Wait helper — poll a condition up to N times with a delay.
#   lib_wait "describe" 60 2   (tries every 2s, up to 60 times)
# The check command must exit 0 for success; it runs under `set +e`.
# ---------------------------------------------------------------------------
lib_wait() {
  local desc="$1" tries="$2" delay="$3"; shift 3
  local i
  for ((i = 1; i <= tries; i++)); do
    if "$@" >/dev/null 2>&1; then
      log_ok "${desc} (after ${i} check(s))"
      return 0
    fi
    sleep "${delay}"
  done
  log_err "${desc} — gave up after $((tries * delay))s"
  return 1
}

# ---------------------------------------------------------------------------
# Resolve repo root and canonical paths relative to this library.
# ---------------------------------------------------------------------------
LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_REPO_ROOT="$(cd "${LIB_DIR}/.." && pwd)"
LIB_SERVICES_YAML="${LIB_SERVICES_YAML:-${LIB_DIR}/services.yaml}"
# The Helm chart every operations script now drives (charts/your-service-name).
LIB_CHART_DIR="${LIB_CHART_DIR:-${LIB_REPO_ROOT}/charts/your-service-name}"
