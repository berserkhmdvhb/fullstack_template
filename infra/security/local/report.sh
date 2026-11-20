#!/usr/bin/env bash
# infra/security/local/report.sh
# =============================================================================
# Human-readable views over the supply-chain scan outputs in output/security.
#
# The pipeline writes machine JSON — a CycloneDX SBOM, the Grype vuln report,
# the OSV-Scanner V2 report and an OpenVEX doc — but no human summary screen.
# This is the "display / filter / search" layer over those files: a one-screen
# verdict per axis, plus a recursive search across every JSON output.
#
# It is READ-ONLY: it never runs a scanner (no docker / daemon / network), so it
# is safe on any ref and cheap to call. Outputs it has not seen degrade to a
# muted "not run yet" line rather than erroring, so `report summary` works the
# moment any subset of the chain has run.
#
# Output dir resolution mirrors devsec.sh: $SEC_OUTPUT_DIR wins, else the
# `output.base` value from config.yaml (output/security), relative to the repo
# root — so the bats suite can point it at a temp dir via SEC_OUTPUT_DIR.
#
# Usage:
#   report.sh summary                # one screen across every axis (default)
#   report.sh sbom | vuln | osv | vex | license
#   report.sh search <pattern>       # grep across all *.json outputs
#   report.sh jq <file> [expr]       # passthrough: jq '<expr>' <file>
# =============================================================================
set -euo pipefail

LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"   # infra/security/local
ROOT="$(dirname "$(dirname "$(dirname "$LOCAL_DIR")")")"                     # repo root (cf devsec.sh)
CONFIG="${SEC_CONFIG:-$ROOT/infra/security/config.yaml}"

# Resolve the output dir exactly as the scan chain does (SEC_OUTPUT_DIR > cfg).
BASE="output/security"
if [ -f "$CONFIG" ] && command -v yq >/dev/null 2>&1; then
  BASE="$(yq -r '.output.base // "output/security"' "$CONFIG" 2>/dev/null || printf '%s' "$BASE")"
fi
OUT="${SEC_OUTPUT_DIR:-$ROOT/$BASE}"

# --- presentation (shared with the runner) -----------------------------------
# Colours, glyphs and their degradation (NO_COLOR / non-TTY / dumb terminal)
# come from the runner's presentation layer, so both surfaces share one
# vocabulary instead of re-deriving it here.
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_ui.sh"
# Origin classification (purl -> OS base / interpreter / app-dep / vendored),
# derived at render time from the SBOM/grype purl — see infra/security/local/_origin.sh.
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_origin.sh"

hdr()  { ui_hdr "$1"; }
bold() { printf '%s%s%s' "$BOLD" "$1" "$RESET"; }

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "ERROR: '$1' not found on PATH — install jq to use this report" >&2; exit 127; }
}

# A muted placeholder for an output the pipeline has not produced yet.
absent() { ui_absent "not run yet — missing $1; run: make $2"; }

usage() {
  cat <<EOF
report.sh — views over the scan outputs in $OUT

  summary                one screen across every axis (default)
  sbom | vuln | osv | vex | license
  origin [tsv]           findings grouped by ORIGIN (purl-derived); tsv = raw rows
  search <pattern>       grep across all *.json outputs (recursive)
  jq <file> [expr]       passthrough: jq '<expr>' <file> (file may be a short name)

Env:  SEC_OUTPUT_DIR   override the output dir (defaults to config output.base)
      NO_COLOR         disable ANSI colour
EOF
}

# --- per-axis views ----------------------------------------------------------
# Each prints a compact block that reads the same standalone and inside summary.

cmd_sbom() {
  local f="$OUT/sbom/backend.image.sbom.cdx.json"
  [ -f "$f" ] || { absent "$f" sbom; return 0; }
  jq -r '
    "  components: \(.components|length)",
    "  format: \(.bomFormat)  spec \(.specVersion)  \(.metadata.component.name // .metadata.timestamp // "?")",
    "  by type:",
    ( .components | group_by(.type) | map("    \(.[0].type): \(length)") | .[] )
  ' "$f"
}

cmd_vuln() {
  local s="$OUT/grype/summary.json"
  # The consolidated summary.json carries the verdict but not the per-match
  # vulnerability ids (CVE-/GHSA-/PYSEC-…); those live in the raw grype JSON(s),
  # so the findings-with-ids listing always reads them (across every SBOM). The
  # id is the handle a developer uses to look up / cite a finding — severity and
  # a fix version alone ("upgrade apt") don't say WHICH vulnerability it is.
  local raws
  # `|| true`: when no scan has run, $OUT/grype is absent and find exits non-zero;
  # under `set -euo pipefail` that would abort the assignment, so pin it to empty.
  raws="$(find "$OUT/grype" -maxdepth 1 -type f -name '*.vuln.grype.json' 2>/dev/null \
          | LC_ALL=C sort || true)"

  if [ -f "$s" ]; then
    jq -r '
      # grype emits severities UPPERCASE (LOW/HIGH/…); the rank map matches that.
      {UNKNOWN:0,NEGLIGIBLE:1,LOW:2,MEDIUM:3,HIGH:4,CRITICAL:5} as $r
      | (.failOn // "high" | ascii_downcase) as $f
      | ({none:99,low:2,medium:3,high:4,critical:5}[$f] // 99) as $thr
      | ([.components[].severityCounts | to_entries[] | select(.value>0) | $r[(.key|ascii_upcase)] // 0] | max // 0) as $max
      | ([.components[].total]  | add // 0) as $total
      | ([.components[].fixable] | add // 0) as $fix
      | "  generated: \(.generatedAt // "?")",
        "  fail-on: \(.failOn // "?")  only-fixed",
        "  findings: \($total)   fixable: \($fix)",
        ( .components[] | "  [\(.group)] \(.component): " + (.severityCounts|to_entries|map("\(.key)=\(.value)")|join(" ")) ),
        "  verdict: \(if $max >= $thr then "FAIL (\($f|ascii_upcase) reached)" else "PASS (below \(.failOn))" end)"
    ' "$s"
  elif [ -n "$raws" ]; then
    jq -r '
      "  matches: \(.matches|length)",
      ( [ .matches[].vulnerability.severity ] | group_by(.) | map("    \(.[0]): \(length)") | .[] ),
      "  fixable: \([ .matches[] | select(.fixStates // [] | any(. == "fixed")) ] | length)"
    ' "$(printf '%s\n' "$raws" | head -1)"
  else
    absent "$s" vuln; return 0
  fi

  # Top findings, id-leading (✗ <ids>  pkg@ver  SEV  fix → ver). Capped to the
  # 5 highest-severity packages and 4 ids each, so a noisy component (openssl)
  # stays scannable. Reads every raw grype JSON so multi-image runs are covered.
  [ -n "$raws" ] || return 0
  local files; mapfile -t files <<< "$raws"

  # Where the findings come from — origin is DERIVED from each match's purl at
  # render time (local/_origin.sh), never stored; see wiki/handling-vulnerabilities.
  # It tells you who owns the fix: interpreter / os-base -> the base-bump loop,
  # app-dep -> your lockfile, vendored -> your tree. See `report.sh origin`.
  echo
  printf '  by origin:'
  origin_summary "${files[@]}" | awk '{ printf " %s", $0 } END { print "" }'

  echo
  jq -s -r '
    {UNKNOWN:0,NEGLIGIBLE:1,LOW:2,MEDIUM:3,HIGH:4,CRITICAL:5} as $r
    | ( map(.matches // []) | add // [] ) as $m
    | ( $m
        | group_by(.artifact.name + ":" + .artifact.version)
        | map({
            pkg: .[0].artifact.name,
            ver: .[0].artifact.version,
            ids: ([ .[].vulnerability.id | select(. != null) ] | unique),
            rank: ([ .[].vulnerability.severity // "UNKNOWN" | ascii_upcase | $r[.] // 0 ] | max),
            fix: ([ .[].vulnerability.fix.versions[]? ] | unique | sort)
          })
        | sort_by(-.rank)
        | .[:5] ) as $top
    | (["UNKNOWN","NEGLIGIBLE","LOW","MEDIUM","HIGH","CRITICAL"]) as $lab
    | $top[]
    | "  ✗ \(
          if (.ids|length) > 0
          then (.ids[0:4] | join(", ")) + (if (.ids|length) > 4 then ", …(+\((.ids|length)-4) more)" else "" end)
          else "?" end
        )  \(.pkg)@\(.ver)  \($lab[.rank])\(if (.fix|length) > 0 then "  fix → \(.fix | join(", "))" else "" end)"
  ' "${files[@]}"
}

cmd_origin() {
  # Findings grouped by ORIGIN (who owns the fix), derived from the purl at render
  # time — this is the scriptable surface the triage/SLA loop consumes, without a
  # stored duplicate (route #3). Default: a human block per bucket, highest-severity
  # first. `origin tsv`: raw TSV rows (bucket, rank, id, pkg@ver, SEV, fix) for piping.
  local mode="${1:-}"
  local raws
  raws="$(find "$OUT/grype" -maxdepth 1 -type f -name '*.vuln.grype.json' 2>/dev/null \
          | LC_ALL=C sort || true)"
  [ -n "$raws" ] || { absent "$OUT/grype" vuln; return 0; }
  local files; mapfile -t files <<< "$raws"
  local rows
  rows="$(origin_rows "${files[@]}")"
  [ -n "$rows" ] || { echo "  no findings"; return 0; }

  if [ "$mode" = "tsv" ]; then
    printf '%s\n' "$rows"
    return 0
  fi

  hdr "VULNERABILITY BY ORIGIN (derived from purl at render time)"
  echo "  who owns the fix: interpreter/os-base -> base-bump loop | app-dep -> your lockfile | vendored -> your tree"
  local b block n tab
  tab=$'\t'   # ANSI-C quoting: uutils `sort`/`awk` reject `sort -t"$(printf '\t')"`.
  # Fix-ownership order, not alphabetical: the base-bump items lead.
  for b in interpreter os-base app-dep vendored unknown; do
    block="$(printf '%s\n' "$rows" | awk -F"$tab" -v b="$b" '$1==b')"
    [ -n "$block" ] || continue
    n="$(printf '%s\n' "$block" | grep -c '')"
    printf '\n  %s — %s finding(s):\n' "$b" "$n"
    # rank is column 2; sort it descending so the worst of the bucket leads.
    printf '%s\n' "$block" | sort -t"$tab" -k2,2nr \
      | awk -F"$tab" '{ printf "    ✗ %-18s %-12s %-8s%s\n", $3, $4, $5, ($6!="" ? "  fix → "$6 : "") }'
  done
}

cmd_osv() {
  # OSV-Scanner V2 report: results[].packages[] each carry .package + .vulnerabilities.
  local f="$OUT/osv/report.json"
  [ -f "$f" ] || { absent "$f" osv; return 0; }
  jq -r '
    "  packages flagged: \([.results[].packages[]?.package] | length)",
    "  findings: \([.results[].packages[]?.vulnerabilities[]?.id] | length)",
    "  MALICIOUS (MAL-*): \(if ([.results[].packages[]?.vulnerabilities[]?.id | select(test("MAL-"))] | length) > 0 then "YES" else "no" end)",
    "  by package (first 6 ids, count in parens):",
    ( .results[].packages[]
      | (.package.name // .package.purl // "?") as $n
      | (.package.version // "?") as $v
      | ([.vulnerabilities[]?.id]) as $ids
      | ($ids | length) as $c
      | "    \($n)@\($v): \($ids[0:6] | join(","))\(if $c > 6 then ", …(+\($c - 6) more)" else "" end)" )
  ' "$f"
}

cmd_vex() {
  local f="$OUT/vex/openvex.json"
  [ -f "$f" ] || { absent "$f" vex; return 0; }
  jq -r '
    "  statements: \(.statements|length)   author: \(.author // "?")",
    ( .statements[]
      | "  [\(.status)] \(.vulnerability_id) — \(.justification // "n/a")" )
  ' "$f"
}

cmd_license() {
  # grant writes under output/security/grant/ — a per-component summary.json plus
  # the full <svc>.license.grant.json. Absent until `make license` has run.
  local d="$OUT/grant"
  [ -d "$d" ] || { absent "$d" license; return 0; }

  local sum="$d/summary.json"
  if [ -f "$sum" ]; then
    jq -r '
      "  generated: \(.generatedAt // "?")  mode: \(.mode // "?")  policy: \(.policy // "?")",
      ( .components[] | "  [\(.status)] \(.component) → \(.report // "?")" )
    ' "$sum"
  fi

  # Enrich with the per-package totals from the full grant report, if present.
  local full
  full="$(find "$d" -maxdepth 1 -type f -name '*.grant.json' | head -1)"
  if [ -n "$full" ]; then
    jq -r '
      .run.targets[0].evaluation as $e
      | "  packages: total=\($e.summary.packages.total) unlicensed=\($e.summary.packages.unlicensed) denied=\($e.summary.packages.denied) ignored=\($e.summary.packages.ignored)",
        "  licenses: unique=\($e.summary.licenses.unique) denied=\($e.summary.licenses.denied) nonSPDX=\($e.summary.licenses.nonSPDX)",
        "  verdict: \(if (($e.summary.packages.denied // 0) > 0) then "FAIL (denied licenses)" else "PASS (no denied licenses)" end)"
    ' "$full"
  fi
}

cmd_summary() {
  hdr "SUPPLY-CHAIN SECURITY REPORT"
  echo "  outputs: $OUT"
  hdr "SBOM (syft)";            cmd_sbom
  hdr "VULNERABILITY (grype)";  cmd_vuln
  hdr "KNOWN-VULN SCA (osv)";   cmd_osv
  hdr "VEX (openvex)";          cmd_vex
  hdr "LICENSE (grant)";        cmd_license
}

# --- search / passthrough ----------------------------------------------------
cmd_search() {
  local pat="${1:-}"
  [ -n "$pat" ] || { echo "usage: report.sh search <pattern>" >&2; exit 2; }
  [ -d "$OUT" ] || { echo "no output dir: $OUT (run: make local)" >&2; exit 1; }
  # Search the primary structured JSON only — the raw *.sarif.json
  # files embed full advisory prose and would flood the results.
  local files
  files="$(find "$OUT" -type f -name '*.json' ! -name '*.sarif.json' | sort)"
  [ -n "$files" ] || { echo "(no JSON outputs yet — run: make local)"; return 0; }
  printf '%s\n' "$files" | while IFS= read -r f; do
    if command -v rg >/dev/null 2>&1; then
      rg -i --color always -H -- "$pat" "$f" || true
    else
      grep -InH --color=always -- "$pat" "$f" || true
    fi
  done
}

cmd_jq() {
  local f="${1:-}" expr="${2:-.}"
  [ -n "$f" ] || { echo "usage: report.sh jq <file> [expr]" >&2; exit 2; }
  # Resolve a name under the output dir if it is not already a real path:
  # a relative path (grype/summary.json) is taken under $OUT; a bare basename
  # (report.json) is searched for under $OUT.
  if [ ! -f "$f" ] && [ "${f#/}" = "$f" ] && [ "${f#./}" = "$f" ]; then
    if [ -f "$OUT/$f" ]; then
      f="$OUT/$f"
    else
      local cand
      cand="$(find "$OUT" -type f -name "$f" 2>/dev/null | head -1)"
      [ -n "$cand" ] && f="$cand"
    fi
  fi
  [ -f "$f" ] || { echo "ERROR: no such file: $1 (and not found under $OUT)" >&2; exit 1; }
  jq "$expr" "$f"
}

# --- dispatch ----------------------------------------------------------------
need jq
cmd="${1:-summary}"; shift || true
case "$cmd" in
  summary)            cmd_summary ;;
  sbom|vuln|osv|vex|license) "cmd_$cmd" ;;
  origin)             cmd_origin "$@" ;;
  search)             cmd_search "$@" ;;
  jq)                 cmd_jq "$@" ;;
  -h|--help|help)     usage ;;
  *) echo "unknown command: $cmd" >&2; echo >&2; usage >&2; exit 2 ;;
esac
