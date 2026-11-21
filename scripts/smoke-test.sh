#!/usr/bin/env bash
# your-service-name — adaptable service template (see wiki/getting-started.md)
# ===========================================================================
# smoke-test.sh — synthetic end-to-end checks against the running stack
# ===========================================================================
# Purpose
#   Exercise the live HTTP surface the way a real client would and report a
#   pass/fail per check — the "did it actually deploy and is it serving?" gate.
#   Runs against any base URL: a port-forward (default http://localhost:8000),
#   the ingress host, or wherever you point --base-url.
#
# Checks (backend API)
#   GET  /health            → 200, live
#   GET  /healthz           → 200, ok
#   POST /echo              → 201, echoes the text back
#   GET  /echo/history      → 200, JSON array
#   GET  /items             → 200, JSON array
#   POST /items → GET /items/{id} → DELETE /items/{id}   (full round-trip)
#
# Output
#   text (default), --format json, or --format junit (for CI test reports).
#   --out FILE writes the report there; exit code is non-zero on any failure.
#
# Inputs (flags)
#   -b, --base-url URL      base URL to test (default: http://localhost:8000)
#       --host NAME         send "Host: NAME" on every request (a port-forward reaches the backend
#                           as 127.0.0.1, which its ALLOWED_HOSTS refuses; send the Service name)
#       --format txt|json|junit   report format (default: txt)
#       --out FILE          write the report to FILE (in addition to stdout)
#   -k, --insecure          allow insecure TLS (self-signed local certs)
#   -t, --timeout SECS      per-request timeout (default: 10)
#   -p, --path prefix       base path prefix (e.g. /api/v1)   (default: /api/v1)
#       --dry-run           print the checks, hit nothing
#   -h, --help
# ===========================================================================
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -Eeuo pipefail
set -o errtrace

# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
trap 'lib_err_trap "smoke-test" "${LINENO}" "${BASH_COMMAND}" "$?"' ERR

# ---- Defaults ---------------------------------------------------------------
BASE_URL="http://localhost:8000"
FORMAT="txt"
OUT_FILE=""
INSECURE="false"
TIMEOUT="10"
PREFIX="/api/v1"
HOST_HEADER=""
DRY_RUN="${DRY_RUN:-false}"

# Results accumulators
declare -a R_NAME=() R_STATUS=() R_OK=() R_DETAIL=()
PASS=0; FAIL=0

print_usage() {
  cat <<'EOF'
Usage: smoke-test.sh [options]

Options:
  -b, --base-url URL      base URL to test (default: http://localhost:8000)
      --host NAME         send "Host: NAME" on every request (a port-forward reaches the backend
                          as 127.0.0.1, which its ALLOWED_HOSTS refuses; send the Service name)
      --format txt|json|junit   report format (default: txt)
      --out FILE          write the report to FILE (in addition to stdout)
  -k, --insecure          allow insecure TLS (self-signed local certs)
  -t, --timeout SECS      per-request timeout (default: 10)
  -p, --path prefix       base path prefix (default: /api/v1)
      --dry-run           print the checks, hit nothing
  -h, --help            show this help

Examples:
  ./scripts/port-forward.sh --only backend &    # in one terminal
  ./scripts/smoke-test.sh --base-url http://localhost:8000

  # through a port-forward to a cluster whose backend checks the Host header
  ./scripts/smoke-test.sh --base-url http://127.0.0.1:8000 --host <release>-backend

  # CI: emit a JUnit report
  ./scripts/smoke-test.sh --format junit --out smoke.xml http://my-ingress
EOF
}

# ---- Flags ------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    -b|--base-url)   BASE_URL="$2"; shift 2 ;;
    --host)          HOST_HEADER="$2"; shift 2 ;;
    --format)        FORMAT="$2"; shift 2 ;;
    --out)           OUT_FILE="$2"; shift 2 ;;
    -k|--insecure)   INSECURE="true"; shift ;;
    -t|--timeout)    TIMEOUT="$2"; shift 2 ;;
    -p|--path)       PREFIX="$2"; shift 2 ;;
    --dry-run)       DRY_RUN="true"; shift ;;
    -h|--help)       print_usage; exit 0 ;;
    *) die "Unknown option: $1 (try --help)" ;;
  esac
done

BASE_URL="${BASE_URL%/}"   # strip trailing slash
# No -f: we inspect status codes ourselves, so a 4xx/5xx must not abort curl.
CURL_OPTS=(-sS --max-time "${TIMEOUT}" -o - -w $'\n%{http_code}')
[ "${INSECURE}" = "true" ] && CURL_OPTS+=(-k)
[ -n "${HOST_HEADER}" ] && CURL_OPTS+=(-H "Host: ${HOST_HEADER}")

# curl that returns "body\n<status>" — body may be multi-line, status is last line.
api() {                           # $1 method  $2 path  [$3 data]
  local method="$1" path="$2" data="${3:-}" out rc=0
  if [ "${DRY_RUN}" = "true" ]; then
    printf '    ⇢ curl %s -X %s %s%s\n' "${CURL_OPTS[*]}" "${method}" "${BASE_URL}${path}" \
      "${data:+ -d ${data}}"
    printf '\n000\n'              # sentinel: dry-run has no status
    return 0
  fi
  if [ -n "${data}" ]; then
    out="$(curl "${CURL_OPTS[@]}" -X "${method}" \
            -H 'Content-Type: application/json' -d "${data}" "${BASE_URL}${path}" 2>/dev/null)" || rc=$?
  else
    out="$(curl "${CURL_OPTS[@]}" -X "${method}" "${BASE_URL}${path}" 2>/dev/null)" || rc=$?
  fi
  if [ "${rc}" -ne 0 ]; then
    printf '\n000\n'              # curl could not complete (e.g. connection refused)
  else
    printf '%s' "${out}"
  fi
}

# ---- Record one check -------------------------------------------------------
# $1 name  $2 method  $3 path  $4 data  $5 expected_status  $6 expected_substr
record() {
  R_NAME+=("$1")
  local resp status body
  resp="$(api "$2" "$3" "$4")"
  status="$(printf '%s' "${resp}" | tail -n1)"
  body="$(printf '%s' "${resp}" | sed '$d')"
  local ok="PASS" detail="status ${status}"
  if [ "${status}" != "$5" ]; then
    ok="FAIL"; detail="expected ${5}, got ${status}; ${body:0:120}"
  elif [ -n "$6" ] && [[ "${body}" != *"$6"* ]]; then
    ok="FAIL"; detail="status ${status}, body missing '${6}'; ${body:0:120}"
  fi
  R_STATUS+=("${status}")
  R_OK+=("${ok}")
  R_DETAIL+=("${detail}")
  [[ "${ok}" == "PASS" ]] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))
}

# ---- The checks -------------------------------------------------------------
run_checks() {
  # Dry-run: preview the requests we *would* send, then stop (no pass/fail).
  if [ "${DRY_RUN}" = "true" ]; then
    log_info "planned requests (dry-run — nothing will be sent):"
    api GET  "${PREFIX}/health"      ""  >/dev/null
    api GET  "${PREFIX}/healthz"     ""  >/dev/null
    api POST "${PREFIX}/echo"        '{"text":"smoke"}' >/dev/null
    api GET  "${PREFIX}/echo/history" "" >/dev/null
    api GET  "${PREFIX}/items"       ""  >/dev/null
    api POST "${PREFIX}/items"       '{"name":"smoke-item","description":"ci smoke","is_active":true}' >/dev/null
    exit 0
  fi
  local p="${PREFIX}"
  record "health (live)"      GET  "${p}/health"     "" 200 ""
  record "healthz (always ok)" GET "${p}/healthz"    "" 200 ""
  record "echo roundtrip"     POST "${p}/echo"       '{"text":"smoke"}' 201 "smoke"
  record "echo history"       GET  "${p}/echo/history" "" 200 ""
  record "items list"         GET  "${p}/items"      "" 200 ""
  # items round-trip: create → read back → delete
  record "item create"        POST "${p}/items" \
    '{"name":"smoke-item","description":"ci smoke","is_active":true}' 201 ""
  # If create succeeded, walk the returned id; otherwise skip the follow-ups.
  if [ "${R_OK[-1]}" = "PASS" ]; then
    local created id
    created="$(printf '%s' "$(api POST "${PREFIX}/items" '{"name":"smoke-item","description":"ci smoke","is_active":true}')" | sed '$d')"
    id="$(printf '%s' "${created}" | python3 -c 'import sys,json
try:
    d=json.load(sys.stdin); print(d.get("id",""))
except Exception: print("")' 2>/dev/null)"
    if [ -n "${id}" ]; then
      record "item get"        GET  "${p}/items/${id}" "" 200 "${id}"
      record "item delete"     DELETE "${p}/items/${id}" "" 204 ""
    else
      R_NAME+=("item get (skipped)"); R_STATUS+=("-"); R_OK+=("SKIP"); R_DETAIL+=("no id from create")
    fi
  fi
}

# ---- Reports ----------------------------------------------------------------
report_txt() {
  printf '%s%-30s %-8s %-8s %s%s\n' "${C_BLD}" "CHECK" "STATUS" "RESULT" "DETAIL" "${C_RST}"
  local i
  for i in "${!R_NAME[@]}"; do
    local color="${C_GREEN}"; [[ "${R_OK[$i]}" == "FAIL" ]] && color="${C_RED}"
    printf '  %-28s %-8s %s%-8s%s %s\n' "${R_NAME[$i]}" "${R_STATUS[$i]}" \
      "${color}" "${R_OK[$i]}" "${C_RST}" "${R_DETAIL[$i]}"
  done
  echo
  log_info "summary: ${PASS} passed, ${FAIL} failed (base: ${BASE_URL}${PREFIX})"
}

report_json() {
  local i first="true"
  printf '{\n  "base_url": "%s",\n  "prefix": "%s",\n  "passed": %d, "failed": %d,\n  "checks": [\n' \
    "${BASE_URL}" "${PREFIX}" "${PASS}" "${FAIL}"
  for i in "${!R_NAME[@]}"; do
    [[ "${first}" == "true" ]] || printf ',\n'; first="false"
    printf '    {"name": "%s", "status": "%s", "result": "%s", "detail": "%s"}' \
      "${R_NAME[$i]}" "${R_STATUS[$i]}" "${R_OK[$i]}" "${R_DETAIL[$i]}"
  done
  printf '\n  ]\n}\n'
}

report_junit() {
  local i total="${#R_NAME[@]}"
  printf '<?xml version="1.0" encoding="UTF-8"?>\n'
  printf '<testsuite name="smoke-test" tests="%d" failures="%d" errors="0">\n' "${total}" "${FAIL}"
  for i in "${!R_NAME[@]}"; do
    printf '  <testcase name="%s" time="0">\n' "${R_NAME[$i]}"
    if [ "${R_OK[$i]}" = "FAIL" ]; then
      printf '    <failure message="%s">expected status %s</failure>\n' \
        "${R_DETAIL[$i]//\"/&quot;}" "${R_STATUS[$i]}"
    fi
    printf '  </testcase>\n'
  done
  printf '</testsuite>\n'
}

emit_report() {
  case "${FORMAT}" in
    json)  report_json ;;
    junit) report_junit ;;
    txt)   report_txt ;;
    *)     die "unknown --format ${FORMAT} (use txt|json|junit)" ;;
  esac
}

# ---- Run --------------------------------------------------------------------
# For machine-readable reports, keep stdout pure: banner/logs go to stderr so
# `smoke-test --format junit > report.xml` yields parseable XML/JSON.
if [[ "${FORMAT}" == "json" || "${FORMAT}" == "junit" ]]; then
  lib_banner "smoke-test" "base=${BASE_URL}  prefix=${PREFIX}  format=${FORMAT}" 1>&2
else
  lib_banner "smoke-test" "base=${BASE_URL}  prefix=${PREFIX}  format=${FORMAT}"
fi
need_cmd curl
run_checks

report="$(emit_report)"
echo "${report}"
[ -n "${OUT_FILE}" ] && { printf '%s\n' "${report}" > "${OUT_FILE}"; log_ok "report written to ${OUT_FILE}"; }

[ "${FAIL}" -eq 0 ] || exit 1
exit 0
