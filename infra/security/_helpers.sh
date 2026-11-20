#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# _helpers.sh — Shared runtime for the security pipeline scripts.
#
# Sourced (never executed) by:
#   - sbom-generate.sh
#   - scan-licenses.sh
#   - scan-vulnerabilities.sh
#
# Provides:
#   - Error trap handler (trap_err)
#   - Logging helpers whose ANSI colour degrades gracefully on CI
#     (NO_COLOR / non-TTY / dumb TERM), so piped CI logs stay clean text
#   - Pre-flight guards: need_cmd, need_docker_daemon, require_env_vars,
#     assert_non_root — each names ALL the problems at once
#   - A *safe* .env parser (parse, never `source` — no code in the file runs)
#   - Secret masking (mask / mask_conn)
#   - Dry-run-aware command runners (run / run_ro)
#   - Interactive confirmation, including a break-glass variant that --yes
#     (LIB_ASSUME_YES) does NOT satisfy
#   - Banners / phase dividers, and a pass/fail/summary gate for CI
#   - Temp-file cleanup registered against an EXIT trap (signal-safe)
#
# Design rule: every function is safe to call under `set -euo pipefail`, so all
# expansions of possibly-unset variables use a `${VAR:-default}` form.
#
# Usage:
#   source "$(dirname "$0")/_helpers.sh"
# -----------------------------------------------------------------------------

# Guard against double-sourcing so re-sourcing stays idempotent.
if [[ -n "${_SEC_HELPERS_SOURCED:-}" ]]; then
  # shellcheck disable=SC2317  # reachable on re-source (this is the idempotent-sourcing guard)
  return 0 2>/dev/null || true
fi
_SEC_HELPERS_SOURCED=1

# These scripts run under bash (#!/usr/bin/env bash). If sourced from a POSIX
# sh, the namerefs and arrays below are a syntax error — say so plainly instead.
# (printf, not error: the logging helpers are defined further down.)
if [[ -z "${BASH_VERSION:-}" ]]; then
  printf '%s\n' "ERROR: _helpers.sh must be sourced from bash." >&2
  exit 1
fi

# ---------- ANSI colors (degrade to nothing on CI) ---------------------------
# Colour is disabled when NO_COLOR is set, when stdout is not a TTY (the CI /
# "piped to a file" case), or when the terminal is "dumb". When disabled every
# colour variable is the empty string, so the existing log/warn/error/success
# helpers — which interpolate these — print clean text with no stray escapes.
if [[ -n "${NO_COLOR:-}" || ! -t 1 || "${TERM:-dumb}" == "dumb" ]]; then
  RED=""; GREEN=""; YELLOW=""; BLUE=""; DIM=""; RESET=""
else
  RED="$(printf '\033[31m')";  GREEN="$(printf '\033[32m')"
  YELLOW="$(printf '\033[33m')"; BLUE="$(printf '\033[34m')"
  DIM="$(printf '\033[2m')";   RESET="$(printf '\033[0m')"
fi

# Timestamps on log lines, toggleable with LIB_TS=0 (handy for golden-file tests).
LIB_TS="${LIB_TS:-1}"

# _ts — the "[HH:MM:SS] " prefix, or nothing when LIB_TS=0.
_ts() {
  [[ "$LIB_TS" == "0" ]] && return 0
  printf '[%s] ' "$(date -u +%H:%M:%S)"
}

# ---------- Logging helpers --------------------------------------------------
timestamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }

log()     { printf '%b%s%s%b\n' "$BLUE"  "$(_ts)" "$*" "$RESET"; }
warn()    { printf '%b%s%s%b\n' "$YELLOW" "$(_ts)" "$*" "$RESET" >&2; }
error()   { printf '%b%s%s%b\n' "$RED"   "$(_ts)" "$*" "$RESET" >&2; }
success() { printf '%b%s%s%b\n' "$GREEN" "$(_ts)" "$*" "$RESET"; }
die()     { error "$*"; exit 2; }

# ---------- Error trap handler ----------------------------------------------
# Consumers install with:  trap 'trap_err' ERR
# Fires under `set -e`/`errtrace` when any command fails; names the failing
# script, line and command. Uses the guarded colour vars, so it is CI-clean too.
trap_err() {
  local rc=$?
  printf '%b[%s] ERROR @ %s:%d: %s (exit %d)%b\n' \
    "$RED" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "${BASH_SOURCE[1]:-}" "${BASH_LINENO[0]}" \
    "${BASH_COMMAND:-<unknown>}" "$rc" "$RESET" >&2
  exit "$rc"
}

# _join ARGS — comma-join the arguments ("a, b, c"), for the guards below.
_join() {
  local out="" arg
  for arg in "$@"; do out+="${out:+, }${arg}"; done
  printf '%s' "$out"
}

# ---------- Pre-flight guards ------------------------------------------------
# need_cmd CMD... — every command must exist; report all the missing ones.
need_cmd() {
  local c missing=()
  for c in "$@"; do
    command -v "$c" >/dev/null 2>&1 || missing+=("$c")
  done
  (( ${#missing[@]} == 0 )) || die "missing required command(s): $(_join "${missing[@]}")"
}

# need_docker_daemon — die with an actionable message unless the Docker daemon
# is reachable, so a later "cannot connect" is not the first thing the user sees.
need_docker_daemon() {
  local out
  out="$(docker info 2>&1 >/dev/null)" && return 0
  if [[ "$out" == *"permission denied"* ]]; then
    die "permission denied on the Docker socket: $(id -un) is not in the 'docker' group (sudo usermod -aG docker $(id -un))."
  fi
  die "cannot reach the Docker daemon — is it running? (systemctl start docker)"
}

# require_env_vars NAME... — every variable must be set and non-empty; report
# all the missing ones at once rather than failing on the first.
require_env_vars() {
  local name missing=()
  for name in "$@"; do
    [[ -n "${!name:-}" ]] || missing+=("$name")
  done
  (( ${#missing[@]} == 0 )) || die "missing required config value(s): $(_join "${missing[@]}")"
}

# assert_non_root — refuse to run as root while a secrets file is on disk. A
# routine deploy should never be root; override with ALLOW_ROOT=1 only on purpose.
assert_non_root() {
  [[ "${ALLOW_ROOT:-0}" == "1" ]] && return 0
  [[ "$(id -u)" -ne 0 ]] || die "refusing to run as root (set ALLOW_ROOT=1 to override)."
}

# ---------- Temp-file cleanup (signal-safe) ----------------------------------
# Credential helpers write short-lived files holding a token. An explicit `rm`
# covers the happy path and the error path, but not Ctrl-C — so one EXIT trap,
# registered here, covers every exit including a signal.
declare -a _LIB_CLEANUP=()

lib_cleanup_register() { _LIB_CLEANUP+=("$1"); }

_lib_cleanup_run() {
  local path
  for path in "${_LIB_CLEANUP[@]:-}"; do
    [[ -n "$path" ]] && rm -rf "$path"
  done
  _LIB_CLEANUP=()
}
trap _lib_cleanup_run EXIT

# ---------- Secret masking ---------------------------------------------------
# mask VALUE — prove a value exists without revealing it: "supersecret" -> "su****".
mask() {
  local s="$1"
  if [[ -z "$s" || ${#s} -le 4 ]]; then printf '****'; else printf '%s****' "${s:0:2}"; fi
}

# mask_conn CONN — mask the password in a URL:
#   "postgresql://u:p@host/db" -> "postgresql://u:****@host/db"
mask_conn() {
  printf '%s' "$1" | sed -E 's#(://[^:/@]+:)[^@]*(@)#\1****\2#'
}

# ---------- Safe .env parser -------------------------------------------------
# safe_load_env FILE ARRNAME — parse KEY=VALUE (optionally `export KEY=VALUE`)
# into the caller's associative array. It does NOT `source` the file, so no
# shell code in it is ever executed: a value like EVIL=$(touch x) is stored
# literally, and a bare command line is skipped. One layer of matching surrounding
# quotes is stripped; an inline `#` is NOT a comment (values legitimately contain
# it), matching docker-compose .env semantics.
#
#   declare -A ENVV=()
#   safe_load_env .env.production ENVV
safe_load_env() {
  local file="$1" arrname="${2:-ENVV}"
  [[ -f "$file" ]] || die "env file not found: ${file}"
  local -n _target_arr="$arrname"

  local lineno=0 line key val
  while IFS= read -r line || [[ -n "$line" ]]; do
    lineno=$(( lineno + 1 ))
    line="${line%$'\r'}"                       # tolerate CRLF
    [[ -z "${line// /}" ]] && continue         # blank
    [[ "$line" == \#* ]] && continue           # full-line comment
    line="${line#export }"                     # optional `export` prefix
    [[ "$line" == *=* ]] || { warn "skipping malformed line ${file}:${lineno}"; continue; }
    key="${line%%=*}"; val="${line#*=}"
    key="${key// /}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] \
      || { warn "skipping invalid key ${file}:${lineno}: ${key}"; continue; }
    if   [[ "$val" == \"*\" ]]; then val="${val:1:${#val}-2}"
    elif [[ "$val" == \'*\' ]]; then val="${val:1:${#val}-2}"
    fi
    _target_arr["$key"]="$val"
  done < "$file"
}

# ---------- Dry-run-aware command runners ------------------------------------
# Every state-mutating external command goes through `run`, so --dry-run prints
# the exact argv and executes nothing. `run_ro` is the read-only counterpart:
# inspecting state is exactly what a dry run needs to do, so it always executes.
DRY_RUN="${DRY_RUN:-false}"

run() {
  if [[ "$DRY_RUN" == "true" ]]; then
    printf '%s⇢ %s\n' "$(_ts)" "$*"
    return 0
  fi
  "$@"
}

run_ro() { "$@"; }

# _show_run CMD [ARG...] — print, under a step banner, the *actual* tool command
# the step is about to run (syft <image>, grype sbom:…, osv-scanner …) with its
# resolved argument values, so a run log shows what really executes rather than
# only the `bash scan-*.sh` wrapper line. The precise argv is assembled inside the
# (frozen) scan scripts, which the wrapper cannot see; this reconstructs the
# canonical invocation from the same inputs the wrapper has (image ref, SBOM path,
# repo root, config), so keep each call in step with the script it precedes.
# Suppressed under --dry-run, where `run` already echoes the exact argv it would
# execute. Colours degrade to clean text on CI (see the ANSI block above).
_show_run() {
  [[ "$DRY_RUN" == "true" ]] && return 0
  printf '%b%s$ %s%b\n' "$BLUE" "$(_ts)" "$*" "$RESET"
}

# ---------- Interactive confirmation -----------------------------------------
# confirm PROMPT — for anything that touches live infrastructure. Bypassed by
# LIB_ASSUME_YES (how CI calls it, i.e. --yes); refuses a non-interactive stdin
# otherwise, and always succeeds under --dry-run since nothing happens anyway.
LIB_ASSUME_YES="${LIB_ASSUME_YES:-false}"

confirm() {
  local prompt="$1"
  if [[ "$DRY_RUN" == "true" ]]; then
    printf '%s⇢ (dry-run) would confirm: %s\n' "$(_ts)" "$prompt"
    return 0
  fi
  [[ "$LIB_ASSUME_YES" == "true" ]] && return 0
  if [[ ! -t 0 ]]; then
    die "refusing to proceed without confirmation on a non-interactive stdin (pass --yes)."
  fi
  local answer=""
  printf '⚠  %s [y/N] ' "$prompt" >&2
  read -r answer <&2 || answer="n"
  case "${answer,,}" in
    y|yes) return 0 ;;
    *)     warn "aborted by user"; return 1 ;;
  esac
}

# confirm_break_glass VAR PROMPT — a confirmation that LIB_ASSUME_YES (--yes)
# does NOT satisfy, for the paths that knowingly ship something unverified. If
# --yes covered these, the safe path and the unsafe one would be one keystroke
# apart in an automated context. Consent is either spelling out the named VAR
# (VAR=1) or typing YES interactively.
confirm_break_glass() {
  local var="$1" prompt="$2"
  if [[ "$DRY_RUN" == "true" ]]; then
    printf '%s⇢ (dry-run) would require break-glass consent: %s\n' "$(_ts)" "$prompt"
    return 0
  fi
  if [[ "${!var:-}" == "1" ]]; then
    warn "${var}=1 — proceeding without the usual guarantee."
    return 0
  fi
  if [[ ! -t 0 ]]; then
    die "refusing on a non-interactive stdin. --yes does not cover this; set ${var}=1 to mean it."
  fi
  local answer=""
  printf '⚠  %s\n' "$prompt" >&2
  printf 'type YES to proceed: ' >&2
  read -r answer <&2 || answer=""
  [[ "$answer" == "YES" ]] && return 0
  warn "aborted — not confirmed"
  return 1
}

# ---------- Banners / phase dividers -----------------------------------------
_SEC_WIDTH=68

# _bar N — N box-drawing dashes, and nothing for N <= 0 (an unguarded `seq 1 0`
# would print one stray dash).
_bar() {
  local n="$1"
  (( n > 0 )) || return 0
  printf '━%.0s' $(seq 1 "$n")
}

# lib_banner TITLE [SUBTITLE] — a framed header, fixed width so it stays aligned
# in a CI log.
lib_banner() {
  local title="$1" subtitle="${2:-}"
  printf '%s┏━ %s%s %s\n'   "$DIM" "$title" "$(_bar $(( _SEC_WIDTH - ${#title} - 4 )))" "$RESET"
  [[ -n "$subtitle" ]] && printf '%s┃ %s%s\n' "$DIM" "$subtitle" "$RESET"
  printf '%s┗%s%s\n\n' "$DIM" "$(_bar "$_SEC_WIDTH")" "$RESET"
}

# lib_sep LABEL — opens each phase.
lib_sep() {
  local label="$1"
  printf '\n%s━━━ %s %s%s\n' "$DIM" "$label" "$(_bar $(( _SEC_WIDTH - ${#label} - 5 )))" "$RESET"
}

# ---------- pass / fail / summary gate ---------------------------------------
# A script records each step with pass/fail and ends with `summary`, which prints
# the lists and returns non-zero if anything failed — so it composes into a CI gate.
declare -a _LIB_PASSED=() _LIB_FAILED=() _LIB_SKIPPED=()

pass()    { _LIB_PASSED+=("$1");  success "PASS     $1"; }
fail()    { _LIB_FAILED+=("$1");   error  "FAIL     $1"; }
# skipped — an advisory layer did not run (its tool was unavailable). It is neither a
# pass nor a fail: it never gates (return stays 0), but it is listed so a "no coverage"
# gap is visible, never buried as a green PASS.
skipped() { _LIB_SKIPPED+=("$1"); warn "SKIPPED  $1 (advisory layer not run — no coverage)"; }

# _skipped_scan OUT_DIR TOOL REASON — degrade an advisory scan to an honest SKIPPED
# verdict when its tool could not be made available: write report.json {status:SKIPPED}
# and warn once. Callers exit 0 after this (advisory — a skipped layer is a visible gap,
# never a gate and never a silent green).
_skipped_scan() {
  local out_dir="$1" tool="$2" reason="$3"
  mkdir -p "$out_dir"
  jq -n --arg t "$tool" --arg r "$reason" \
    '{tool:$t, status:"SKIPPED", reason:$r}' > "$out_dir/report.json"
  warn "⚠ $tool unavailable — scan SKIPPED: $reason"
}

summary() {
  lib_sep "summary"
  if (( ${#_LIB_PASSED[@]} > 0 )); then
    success "passed: $(_join "${_LIB_PASSED[@]}")"
  fi
  if (( ${#_LIB_SKIPPED[@]} > 0 )); then
    warn "skipped: $(_join "${_LIB_SKIPPED[@]}")"
  fi
  if (( ${#_LIB_FAILED[@]} > 0 )); then
    error "failed: $(_join "${_LIB_FAILED[@]}")"
    return 1
  fi
  [[ "$DRY_RUN" == "true" ]] && warn "dry-run: nothing was executed"
  return 0
}

# ---------- (existing) config loader + output resolution ---------------------
# Reads a yq expression from the YAML config file. Falls back gracefully if yq
# is unavailable.
CONFIG_FILE="${CONFIG_FILE:-infra/security/config.yaml}"
cfg() {
  # An `if`, not `command -v yq && yq … || true`: `A && B || C` is not
  # if-then-else (C runs when A is true and B fails — SC2015). Best-effort read:
  # print the value when yq exists, otherwise nothing; always exit 0.
  if command -v yq >/dev/null 2>&1; then
    yq -r "$1" "$CONFIG_FILE" 2>/dev/null || true
  fi
}

# Resolves SEC_OUTPUT_DIR from config.yaml, falling back to "output/security".
# Callers may override via the SEC_OUTPUT_DIR environment variable.
resolve_sec_output_dir() {
  if [[ -n "${SEC_OUTPUT_DIR:-}" ]]; then
    return
  fi
  SEC_OUTPUT_DIR="$(cfg '.output.base')"
  # An `if`, not `[[ -z ]] && X=...`: the latter is the last statement of this
  # function, and under `set -e` a false `[[ ]]` there trips the ERR trap (a
  # long-standing bash gotcha for `cmd && assign` as a function's final
  # statement). This makes the shared runtime safe to call from a consumer that
  # runs `set -Eeuo pipefail`, which is every one of them.
  if [[ -z "$SEC_OUTPUT_DIR" ]]; then
    SEC_OUTPUT_DIR="output/security"
  fi
}
