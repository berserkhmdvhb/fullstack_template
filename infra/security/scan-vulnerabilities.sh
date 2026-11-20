#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Portable Vulnerability Scan (multi-SBOM, per-image and per-component aware)
#
# Scans ALL SBOMs under $SEC_OUTPUT_DIR/sbom (or SBOM_DIR) with Anchore Grype,
# emits per-SBOM JSON + SARIF, and produces machine- and human-readable
# summaries. Fails the job if findings >= FAIL_LEVEL (default: high).
#
# Requirements: grype, jq
# Install (CI/local): pin + verify checksum via infra/security/tools-install.sh grype <version>
# (never `curl | sh` — see wiki/ROADMAP-supply-chain.md §4, where that legacy debt is retired)
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
OUT_GRYPE_DIR="${OUT_BASE}/grype"
OUT_LOG_DIR="${OUT_BASE}/logs"

FAIL_LEVEL="${FAIL_LEVEL:-$(cfg '.policy.fail_level')}"
[[ -z "$FAIL_LEVEL" ]] && FAIL_LEVEL="high"

ONLY_FIXED="${ONLY_FIXED:-$(cfg '.policy.only_fixed')}"
[[ -z "$ONLY_FIXED" ]] && ONLY_FIXED="false"

QUIET="${QUIET:-false}"
VERBOSE="${VERBOSE:-false}"
GRYPE_CONFIG="${GRYPE_CONFIG:-}"

# Recommended cache
GRYPE_DB_CACHE_DIR="${GRYPE_DB_CACHE_DIR:-${GITHUB_WORKSPACE:-$PWD}/.cache/grype/db}"
GRYPE_CHECK_FOR_APP_UPDATE="${GRYPE_CHECK_FOR_APP_UPDATE:-false}"
export GRYPE_DB_CACHE_DIR GRYPE_CHECK_FOR_APP_UPDATE

usage() {
  cat <<'USAGE'
Usage: scan-vulnerabilities.sh [-s SBOM_DIR] [-O OUTPUT_DIR] [-l LEVEL]
                              [-F] [-c CONFIG] [-q] [-v] [-h]

Description:
  Scan all SBOM files using Anchore Grype and generate:
    - Per-component vulnerability reports (JSON + SARIF)
    - Summary outputs (text + JSON)
    - Actionable reports (package-based, component-based, flat)

Options:
  -s  SBOM directory to scan (default: output/security/sbom)
  -O  Output base directory (default: output/security)
  -l  Fail level threshold (negligible|low|medium|high|critical)
  -F  Only report fixable vulnerabilities
  -c  Path to Grype config file
  -q  Quiet mode (minimal output)
  -v  Verbose mode (debug tracing)
  -h  Show this help message

Environment:
  SEC_OUTPUT_DIR     Base output directory
  SBOM_DIR           Override SBOM location
  FAIL_LEVEL         Severity threshold (default: high)
  ONLY_FIXED         true/false
  GRYPE_CONFIG       Path to Grype config
  CONFIG_FILE        Path to YAML config (default: infra/security/config.yaml)

Outputs:
  ${OUT_BASE}/grype/
    ├── *.vuln.grype.json           (per-component findings)
    ├── *.vuln.sarif.json           (SARIF reports)
    ├── summary.txt                 (human-readable summary)
    ├── summary.json                (machine-readable summary)
    ├── vuln-by-package.txt         (package aggregation)
    ├── vuln-by-component.txt       (per-component breakdown)
    └── vuln-findings-raw.txt       (raw findings)

Behavior:
  - All SBOMs found in SBOM_DIR are scanned
  - Job fails if vulnerabilities >= FAIL_LEVEL
  - Grouping of components is config-driven

Examples:
  scan-vulnerabilities.sh
  scan-vulnerabilities.sh -l critical
  scan-vulnerabilities.sh -F -q
  scan-vulnerabilities.sh -s custom/sbom -O reports

USAGE
  exit 0
}

lib_banner "scan-vulnerabilities" "Multi-SBOM vulnerability scan (grype, per-component)"

while getopts ":s:O:l:Fc:qvh" opt; do
  case "$opt" in
    s) SBOM_DIR="$OPTARG" ;;
    O) OUT_BASE="$OPTARG"; OUT_GRYPE_DIR="$OUT_BASE/grype"; OUT_LOG_DIR="$OUT_BASE/logs" ;;
    l) FAIL_LEVEL="$OPTARG" ;;
    F) ONLY_FIXED=true ;;
    c) GRYPE_CONFIG="$OPTARG" ;;
    q) QUIET=true ;;
    v) VERBOSE=true ;;
    h) usage ;;
    *) error "Invalid option -$OPTARG" ;;
  esac
done
$VERBOSE && set -x

case "${FAIL_LEVEL,,}" in negligible|low|medium|high|critical) : ;; *) die "Invalid -l ${FAIL_LEVEL}";; esac

# ---------- Pre-flight ----------
# Shared need_cmd names every missing command at once (this script sources the
# runtime), replacing the inline `command -v … || die` duplication.
need_cmd grype jq
mkdir -p "$OUT_GRYPE_DIR" "$OUT_LOG_DIR"
LOG_FILE="${OUT_LOG_DIR}/grype-$(date +%Y%m%d-%H%M%S).log"; : > "$LOG_FILE"
[[ -d "$SBOM_DIR" ]] || die "SBOM directory not found: $SBOM_DIR"
$QUIET || log "Logging to: $LOG_FILE"

# Mirror all stderr to log (keep fd3 as original stderr for final exit path)
exec 3>&2
exec 2> >(tee -a "$LOG_FILE" >&2)

log "Starting vulnerability scan…"

# ---------- Collect SBOMs (ignore manifest), deterministic order ----------
mapfile -t SBOMS < <(
  find "$SBOM_DIR" -maxdepth 1 -type f -name '*.json' ! -name 'index.json' \
  | LC_ALL=C sort
)

if [[ ${#SBOMS[@]} -eq 0 ]]; then
  die "No SBOM files found in $SBOM_DIR"
fi

# ---------- Config-driven grouping ----------
GROUPING() {
  local file="$1"
  local filename
  filename="$(basename "$file")"

  # Read yq's output into a var first: the early `return` below otherwise closes the
  # pipe mid-stream and yq dies with SIGPIPE (exit 141), logged as a spurious ERROR.
  local rules
  rules="$(yq -r '.grouping.rules[] | "\(.match)|\(.group)"' "$CONFIG_FILE")" || return

  while IFS="|" read -r match group; do
    if [[ "$filename" == *"$match"* ]]; then
      echo "$group"
      return
    fi
  done <<< "$rules"

  echo "OTHER COMPONENT"
}
# ---------- Component name extraction (for output naming) ----------
extract_component() {
  local filename="$1"
  local name="${filename%.json}"
  echo "$name" | sed -E 's/\.sbom\..*$//'
}

# ---------- Scanner ----------
scan_one() {
  local sbom out_json out_sarif rc
  sbom="$1"
  filename="$(basename "$sbom")"

  # extract component cleanly
  component="$(extract_component "$filename")"
  out_json="${OUT_GRYPE_DIR}/${component}.vuln.grype.json"
  out_sarif="${OUT_GRYPE_DIR}/${component}.vuln.sarif.json"
  # Build args
  local json_args=( "sbom:${sbom}" -o json )
  local sarif_args=( "sbom:${sbom}" -o sarif )

  if [[ "$ONLY_FIXED" == "true" ]]; then
    json_args+=( --only-fixed )
    sarif_args+=( --only-fixed )
  fi
  [[ -n "$GRYPE_CONFIG" ]] && json_args+=( --config "$GRYPE_CONFIG" ) && sarif_args+=( --config "$GRYPE_CONFIG" )


  # Broaden matching only for application SBOMs; keep images strict

  group="$(GROUPING "$sbom")"

  case "$group" in
    "CONTAINER IMAGE") : ;;
    *) json_args+=( --add-cpes-if-none ); sarif_args+=( --add-cpes-if-none ) ;;
  esac

  $QUIET || log "Scanning $(basename "$sbom")"
  {
    if $QUIET; then
      grype "${json_args[@]}" > "$out_json" 2>>"$LOG_FILE"
      grype "${sarif_args[@]}" > "$out_sarif" 2>>"$LOG_FILE"
    else
      grype "${json_args[@]}" | tee "$out_json" >/dev/null 2>>"$LOG_FILE"
      grype "${sarif_args[@]}" | tee "$out_sarif" >/dev/null 2>>"$LOG_FILE"
    fi
  } 2>>"$LOG_FILE"

  local fail_args=( "sbom:${sbom}" --fail-on "$FAIL_LEVEL" -o table )
  if [[ "$ONLY_FIXED" == "true" ]]; then
    fail_args+=( --only-fixed )
  fi
  
  [[ -n "$GRYPE_CONFIG" ]] && fail_args+=( --config "$GRYPE_CONFIG" )

  case "$group" in
    "CONTAINER IMAGE") : ;;
    *) fail_args+=( --add-cpes-if-none ) ;;
  esac

  set +e
  grype "${fail_args[@]}" >/dev/null 2>>"$LOG_FILE"
  rc=$?
  set -e
  return $rc
}

# ---------- Run ----------
unset GROUPS 2>/dev/null || true
declare -A RESULTS
declare -A GROUPS
failed=0
shopt -s nullglob
for sbom in "${SBOMS[@]}"; do
  group="$(GROUPING "$sbom")"
  GROUPS["$sbom"]="$group"

  if scan_one "$sbom"; then
    RESULTS["$sbom"]="PASS"
  else
    RESULTS["$sbom"]="FAIL"
    failed=$((failed+1))
  fi
done

# Verify expected JSON outputs exist
missing=()
for sbom in "${SBOMS[@]}"; do
  filename="$(basename "$sbom")"

  # extract component cleanly
  component="$(extract_component "$filename")"
  [[ -f "${OUT_GRYPE_DIR}/${component}.vuln.grype.json" ]] || missing+=("$component")
  [[ -f "${OUT_GRYPE_DIR}/${component}.vuln.sarif.json" ]] || missing+=("$component-sarif")
done
if [[ ${#missing[@]} -gt 0 ]]; then
  printf 'Missing grype outputs for:\n' >&2
  printf ' - %s\n' "${missing[@]}" >&2
  die "Aborting due to missing outputs."
fi

# ---------- Summaries ----------
SUMMARY_TXT="${OUT_GRYPE_DIR}/summary.txt"
{
  echo "============================================================"
  echo " Grype Summary (per component)"
  echo "============================================================"
  echo "Generated: $(timestamp)"
  if [[ "$ONLY_FIXED" == "true" ]]; then
    echo "Fail-on: ${FAIL_LEVEL}, only-fixed"
  else
    echo "Fail-on: ${FAIL_LEVEL}"
  fi
  echo
  for sbom in "${SBOMS[@]}"; do
    filename="$(basename "$sbom")"
    component="$(extract_component "$filename")"
    group="${GROUPS[$sbom]}"
    echo "[$group]"
    echo "SBOM: $filename"
    echo "Status: ${RESULTS[$sbom]}"
    echo "Histogram:"
    json="${OUT_GRYPE_DIR}/${component}.vuln.grype.json"
    
    
    while IFS=": " read -r sev count; do
      case "$sev" in
        CRITICAL|HIGH) color="$RED" ;;
        MEDIUM)        color="$YELLOW" ;;
        LOW)           color="$BLUE" ;;
        NEGLIGIBLE|UNKNOWN) color="$DIM" ;;
        *)             color="$RESET" ;;
      esac
      printf "%b%s%b: %s\n" "$color" "$sev" "$RESET" "$count"
    done < <( jq -r '
      (.matches // []) as $m
      | [ $m[].vulnerability.severity // "UNKNOWN" | ascii_upcase ]
      | sort | group_by(.) | map({key: .[0], count: length})
      | sort_by(.key) | .[]
      | "\(.key): \(.count)"
    ' "$json" )
    echo
  done
  echo "============================================================"
  echo "Failures >= threshold: $failed"
  echo "============================================================"
} > "$SUMMARY_TXT"
$QUIET || cat "$SUMMARY_TXT"

SUMMARY_JSON="${OUT_GRYPE_DIR}/summary.json"

(
  echo '{'
  printf ' "generatedAt": "%s",\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf ' "failOn": "%s",\n' "$FAIL_LEVEL"

  echo ' "components": ['

  first=true
  for file in "$OUT_GRYPE_DIR"/*.vuln.grype.json; do
    $first || echo ','
    first=false

    filename="$(basename "$file")"
    component="${filename%.vuln.grype.json}"

    group="OTHER COMPONENT"
    for sbom in "${SBOMS[@]}"; do
      if [[ "$(extract_component "$(basename "$sbom")")" == "$component" ]]; then
        group="${GROUPS[$sbom]}"
        break
      fi
    done
    jq -r --arg component "$component" --arg group "$group" '
      .matches as $m |

      # severity counts
      (
        [ $m[].vulnerability.severity // "UNKNOWN" | ascii_upcase ]
        | group_by(.)
        | map({key: .[0], value: length})
        | from_entries
      ) as $counts |

      # top packages
      (
        $m
        | group_by(.artifact.name + ":" + .artifact.version)
        | map({
            name: .[0].artifact.name,
            count: length
          })
        | sort_by(-.count)
        | .[:3]
      ) as $topPackages |

      # fixable
      (
        [ $m[]
          | select(.vulnerability.fix.state == "fixed")
        ] | length
      ) as $fixable |

      {
        component: $component,
        group: $group,
        total: ($m | length),
        fixable: $fixable,
        severityCounts: $counts,
        topPackages: $topPackages
      }
    ' "$file"

  done

  echo
  echo ' ]'
  echo '}'
) > "$SUMMARY_JSON"

REPORT_BY_PACKAGE="${OUT_GRYPE_DIR}/vuln-by-package.txt"

{
  echo "============================================================"
  echo " VULNERABILITY REPORT (ACTIONABLE)"
  echo "============================================================"
  echo "Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "Fail-on: $FAIL_LEVEL"
  echo

  echo "=== 🎬 ACTION REQUIRED ==="
  echo

  # Collect each grype report into an array so it reaches jq as its own argument
  # (avoids unquoted $(...) word-splitting). Built once, reused by both passes.
  mapfile -t vuln_reports < <(printf '%s\n' "$OUT_GRYPE_DIR"/*.vuln.grype.json | LC_ALL=C sort)

  jq -s -r '
    map(.matches // []) | flatten
    | sort_by(.artifact.name + ":" + .artifact.version)
    | group_by(.artifact.name + ":" + .artifact.version)
    | map({
        package: .[0].artifact.name,
        installed: .[0].artifact.version,
        vulnerabilities: map(.vulnerability) | unique_by(.id),
        fix_versions: [ .[].vulnerability.fix.versions[]? ] | unique | sort,

        highest_cvss: (
          [ .[].vulnerability.cvss[0].metrics.baseScore // 0 ]
          | max
        ),

        highest_label: (
          [ .[].vulnerability.cvss[0].metrics.baseScore // 0 ]
          | max
          | if . >= 9 then "CRITICAL"
            elif . >= 7 then "HIGH"
            elif . >= 4 then "MEDIUM"
            elif . > 0 then "LOW"
            else "UNKNOWN"
            end
        )
    })
    | .[]
    | "PACKAGE: \(.package) (\(.installed))\n" +
      "  Vulnerabilities: \(.vulnerabilities | length)\n" +
      "  Highest CVSS: \(.highest_cvss)\n" +
      "  Severity: \(.highest_label)\n" +
      "  Fix versions: \(
          if (.fix_versions | length) > 0
          then (.fix_versions | join(", "))
          else "none"
          end
        )\n"
  ' "${vuln_reports[@]}"
  echo
  echo "=== 📊 SUMMARY ==="
  echo

  jq -s -r '
    map(.matches // [])
    | flatten
    | map((.vulnerability.severity // "UNKNOWN") | ascii_upcase)
    | group_by(.)
    | map({key: .[0], count: length})
    | sort_by(.key)
    | .[]
    | "\(.key): \(.count)"
  ' "${vuln_reports[@]}"

  echo
  echo "============================================================"
  echo "NOTE:"
  echo "- Many vulnerabilities originate from same package (e.g. openssl, axios)"
  echo "- Focus on upgrading dependencies rather than individual CVEs"
  echo "============================================================"

} > "$REPORT_BY_PACKAGE"


REPORT_BY_COMPONENT="${OUT_GRYPE_DIR}/vuln-by-component.txt"

{
  echo "============================================================"
  echo " VULNERABILITY REPORT (DETAILED BY COMPONENT)"
  echo "============================================================"
  echo "Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "Fail-on: $FAIL_LEVEL"
  echo
  for file in "$OUT_GRYPE_DIR"/*.vuln.grype.json; do
    filename="$(basename "$file")"
    component="${filename%.vuln.grype.json}"

    echo "============================================================"
    echo " COMPONENT: $component"
    echo "============================================================"

    jq -r '
      .matches as $m
      | $m
      | sort_by(.artifact.name + ":" + .artifact.version)
      | group_by(.artifact.name + ":" + .artifact.version)
      | map({
          package: .[0].artifact.name,
          installed: .[0].artifact.version,
          vulnerabilities: map(.vulnerability) | unique_by(.id),
          fix_versions: [ .[].vulnerability.fix.versions[]? ] | unique | sort,

          highest_cvss: (
            [ .[].vulnerability.cvss[0].metrics.baseScore // 0 ]
            | max
          ),

          highest_label: (
            [ .[].vulnerability.cvss[0].metrics.baseScore // 0 ]
            | max
            | if . >= 9 then "CRITICAL"
              elif . >= 7 then "HIGH"
              elif . >= 4 then "MEDIUM"
              elif . > 0 then "LOW"
              else "UNKNOWN"
              end
          )
      })
      | .[]
      | "PACKAGE: \(.package) (\(.installed))\n" +
        "  Vulnerabilities: \(.vulnerabilities | length)\n" +
        "  Highest CVSS: \(.highest_cvss)\n" +
        "  Severity: \(.highest_label)\n" +
        "  Fix versions: \(
            if (.fix_versions | length) > 0
            then (.fix_versions | join(", "))
            else "none"
            end
          )\n"
    ' "$file"

    echo
  done

} > "$REPORT_BY_COMPONENT"

REPORT_RAW="${OUT_GRYPE_DIR}/vuln-findings-raw.txt"

{
  echo "COMPONENT | PACKAGE | VERSION | SEVERITY | CVE"
  for file in "$OUT_GRYPE_DIR"/*.vuln.grype.json; do
    component="$(basename "$file" .vuln.grype.json)"

    jq -r --arg comp "$component" '
      .matches[]
      | [
          $comp,
          .artifact.name,
          .artifact.version,
          (.vulnerability.severity // "UNKNOWN"),
          .vulnerability.id
        ]
      | @tsv
    ' "$file"

  done

} > "$REPORT_RAW"


if [[ "$failed" -gt 0 ]]; then
  error "Vulnerabilities detected >= ${FAIL_LEVEL}."
  exec 2>&3
  exit 1
fi
success "No vulnerabilities found >= ${FAIL_LEVEL}."
exec 2>&3

