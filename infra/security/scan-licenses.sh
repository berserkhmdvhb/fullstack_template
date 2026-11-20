#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Portable License Scan (Grant, SBOM-driven, fully generalized)
#
# - Scans ALL SBOMs (image / project / root)
# - Supports:
#     • Inventory mode (list)
#     • Enforcement mode (check)
# - Produces:
#     • per-component JSON
#     • summary.txt
#     • summary.json
#
# Output naming:
#   <component>.license.grant.json
#
# Requirements: grant, jq
# -----------------------------------------------------------------------------
set -Eeuo pipefail

# ---------- Shared helpers ----------
# shellcheck source=/dev/null
source "$(dirname "$0")/_helpers.sh"
trap 'trap_err' ERR

# ---------- Defaults ----------
resolve_sec_output_dir

SBOM_DIR="${SBOM_DIR:-${SEC_OUTPUT_DIR}/sbom}"
OUT_BASE="${OUT_BASE:-${SEC_OUTPUT_DIR}}"

OUT_GRANT_DIR="${OUT_BASE}/grant"
OUT_LOG_DIR="${OUT_BASE}/logs"

GRANT_CONFIG="${GRANT_CONFIG:-$(cfg '.license.policy_file')}"
[[ -z "$GRANT_CONFIG" && -f "infra/security/grant.yaml" ]] && GRANT_CONFIG="infra/security/grant.yaml"

LIST_ONLY=false
QUIET=false
VERBOSE=false

# ---------- CLI ----------
usage() {
  cat <<'USAGE'
Usage:
  scan-licenses.sh [-s SBOM_DIR] [-O OUT_BASE] [-c POLICY] [-L] [-q] [-v] [-h]

Description:
  Scan SBOMs for license compliance using Anchore Grant.

Modes:
  list   → inventory licenses
  check  → enforce license policy (requires config)

Options:
  -s  SBOM directory (default: output/security/sbom)
  -O  Output base directory
  -c  Grant policy YAML (enables enforcement)
  -L  Force list-only mode
  -q  Quiet
  -v  Verbose
  -h  Help

Outputs:
  grant/
    *.license.grant.json
    summary.txt
    summary.json
USAGE
  exit 0
}

lib_banner "scan-licenses" "Portable license compliance scan (grant, SBOM-driven)"

while getopts ":s:O:c:Lqvh" opt; do
  case "$opt" in
    s) SBOM_DIR="$OPTARG" ;;
    O) OUT_BASE="$OPTARG"; OUT_GRANT_DIR="$OUT_BASE/grant"; OUT_LOG_DIR="$OUT_BASE/logs" ;;
    c) GRANT_CONFIG="$OPTARG" ;;
    L) LIST_ONLY=true ;;
    q) QUIET=true ;;
    v) VERBOSE=true ;;
    h) usage ;;
    *) die "Invalid option -$OPTARG" ;;
  esac
done

$VERBOSE && set -x

# ---------- Pre-flight ----------
# Shared need_cmd names every missing command at once (this script sources the
# runtime), replacing the inline `command -v … || die` duplication.
need_cmd grant jq

mkdir -p "$OUT_GRANT_DIR" "$OUT_LOG_DIR"

LOG_FILE="${OUT_LOG_DIR}/grant-$(date +%Y%m%d-%H%M%S).log"
: > "$LOG_FILE"

[[ -d "$SBOM_DIR" ]] || die "SBOM directory not found: $SBOM_DIR"

$QUIET || log "Logging to: $LOG_FILE"

# Mirror stderr
exec 3>&2
exec 2> >(tee -a "$LOG_FILE" >&2)

log "Starting license scan…"

# ---------- Helpers ----------
extract_component() {
  local filename="$1"
  echo "${filename%.json}" | sed -E 's/\.sbom\..*$//'
}

# ---------- Collect SBOMs ----------
mapfile -t SBOMS < <(
  find "$SBOM_DIR" -maxdepth 1 -type f -name '*.json' ! -name 'index.json' \
  | LC_ALL=C sort
)

if [[ ${#SBOMS[@]} -eq 0 ]]; then
  die "No SBOM files found in $SBOM_DIR"
fi

# ---------- Mode ----------
MODE="list"

if [[ -n "$GRANT_CONFIG" && "$LIST_ONLY" == "false" ]]; then
  MODE="check"
fi

$QUIET || log "Grant mode: $MODE"

# ---------- Scan ----------
declare -A RESULTS
failed=0

scan_one() {
  local sbom="$1"
  local filename component out_json

  filename="$(basename "$sbom")"
  component="$(extract_component "$filename")"
  out_json="${OUT_GRANT_DIR}/${component}.license.grant.json"

  # ALWAYS generate inventory (guaranteed output)
  grant list "$sbom" -o json -f "$out_json" 2>>"$LOG_FILE" \
    || return 1

  # If enforcement mode: evaluate policy
  if [[ "$MODE" == "check" ]]; then
    set +e
    grant check "$sbom" -c "$GRANT_CONFIG" >/dev/null 2>>"$LOG_FILE"
    rc=$?
    set -e
    return $rc
  fi

  return 0
}
# Execute scans
shopt -s nullglob
for sbom in "${SBOMS[@]}"; do
  $QUIET || log "Scanning $(basename "$sbom")"

  set +e
  if scan_one "$sbom"; then
    RESULTS["$sbom"]="PASS"
  else
    RESULTS["$sbom"]="FAIL"
    failed=$((failed+1))
  fi
  set -e
done

# ---------- Validation ----------
missing=()
shopt -s nullglob
for sbom in "${SBOMS[@]}"; do
  component="$(extract_component "$(basename "$sbom")")"
  [[ -f "${OUT_GRANT_DIR}/${component}.license.grant.json" ]] || missing+=("$component")
done

if [[ ${#missing[@]} -gt 0 ]]; then
  printf "Missing Grant outputs:\n" >&2
  printf " - %s\n" "${missing[@]}" >&2
  die "Grant output validation failed"
fi

# ---------- Summary TXT ----------
SUMMARY_TXT="${OUT_GRANT_DIR}/summary.txt"

{
  echo "============================================================"
  echo " License Scan Summary (Grant)"
  echo "============================================================"
  echo "Generated: $(timestamp)"
  echo "Mode: $MODE"
  [[ -n "$GRANT_CONFIG" ]] && echo "Policy: $GRANT_CONFIG"
  echo
  shopt -s nullglob
  for sbom in "${SBOMS[@]}"; do
    filename="$(basename "$sbom")"
    component="$(extract_component "$filename")"
    report="${OUT_GRANT_DIR}/${component}.license.grant.json"

    echo "------------------------------------------------------------"
    echo "Component: $component"
    echo "SBOM: $filename"
    echo "Status: ${RESULTS[$sbom]}"
    echo

    if [[ -f "$report" ]]; then
      echo "License Risk Summary:"

      jq -r '
        if has("riskSummary") then
          .riskSummary
          | to_entries[]
          | "  \(.key): \(.value)"
        else
          [.. | objects | select(has("risk")) | .risk]
          | map(ascii_upcase)
          | group_by(.)
          | map({key: .[0], count: length})
          | sort_by(.key)
          | .[]
          | "  \(.key): \(.count)"
        end
      ' "$report" 2>/dev/null || echo "  (no risk data)"

      # total licenses count (best effort)
      total=$(jq '[.. | objects | select(has("license"))] | length' "$report" 2>/dev/null || echo 0)
      echo "Total license entries: $total"
    fi

    echo
  done

  echo "============================================================"
  echo "Policy violations: $failed"
  echo "============================================================"

} > "$SUMMARY_TXT"

$QUIET || cat "$SUMMARY_TXT"

# ---------- Summary JSON ----------
SUMMARY_JSON="${OUT_GRANT_DIR}/summary.json"

(
  echo '{'
  printf ' "generatedAt": "%s",\n' "$(timestamp)"
  printf ' "mode": "%s",\n' "$MODE"

  if [[ -n "$GRANT_CONFIG" ]]; then
    printf ' "policy": %s,\n' "$(jq -Rn --arg c "$GRANT_CONFIG" '$c')"
  else
    printf ' "policy": null,\n'
  fi
  echo ' "components": ['

  first=true
  shopt -s nullglob
  for sbom in "${SBOMS[@]}"; do
    $first || echo ','
    first=false

    component="$(extract_component "$(basename "$sbom")")"
    report="${OUT_GRANT_DIR}/${component}.license.grant.json"

    jq -n \
      --arg component "$component" \
      --arg status "${RESULTS[$sbom]}" \
      --arg report "$(basename "$report")" \
      '
      {
        component: $component,
        status: $status,
        report: $report
      }'
  done

  echo
  echo ' ]'
  echo '}'
) > "$SUMMARY_JSON"

# ---------- Exit ----------
if [[ "$MODE" == "check" && "$failed" -gt 0 ]]; then
  error "License policy violations detected."
  exec 2>&3
  exit 1
fi

success "License scan complete. Mode=$MODE Violations=$failed"

# Restore stderr
exec 2>&3