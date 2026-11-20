#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# scan-osv.sh — Source-level SCA + known-malicious scan with OSV-Scanner.
#
# OSV-Scanner (google/osv-scanner) is the official frontend to OSV.dev: one
# checksum-verified, offline-capable, multi-language lockfile scanner. It covers
# known-CVE SCA (npm, here, from package-lock.json) and carries the OSV MAL-*
# known-malicious corpus — the same feed the hand-rolled matcher used, but
# maintained and multi-language. It reads RESOLVED lockfiles (package-lock.json,
# requirements.txt, poetry.lock, …); it does NOT parse PEP 621 pyproject.toml
# [project].dependencies, so pip_audit stays the Python known-CVE SCA (see
# wiki/supply-chain-tooling-assessment.md).
#
# This is the SINGLE SOURCE OF TRUTH for the OSV scan: the CI `osv_scan` job and
# the local `devsec.sh osv` command both call it, so CI and a laptop never drift.
#
# Advisory (exit 0): a finding is reported, not a hard gate — the CI job is
# allow_failure: true. A non-zero scan exit is captured as the verdict and the
# reports are still written first.
#
# Usage: scan-osv.sh [SEC_OUTPUT_DIR] [PROJECT_ROOT]
#   SEC_OUTPUT_DIR  security output dir  (default: config.yaml output.base)
#   PROJECT_ROOT    repo root            (default: this script's ../..)
# -----------------------------------------------------------------------------
set -Eeuo pipefail

# ---------- Shared runtime ----------
# This script lives in infra/security/local/, so _helpers.sh is one level up.
# shellcheck source=/dev/null
source "$(dirname "$(dirname "${BASH_SOURCE[0]}")")/_helpers.sh"
trap 'trap_err' ERR

# --help short-circuit (usage() is defined above).
usage() {
  cat <<'USAGE'
Usage: scan-osv.sh [SEC_OUTPUT_DIR] [PROJECT_ROOT]

Source-level SCA + known-malicious (OSV-Scanner / OSV.dev). Advisory: a finding
is reported, not a hard gate (exit 0).

  SEC_OUTPUT_DIR  security output dir  (default: config.yaml output.base)
  PROJECT_ROOT    repo root            (default: this script's ../..)
USAGE
  exit 0
}
case "${1:-}" in -h|--help) usage ;; esac

# ---------- Defaults ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEC_DIR="$(dirname "$SCRIPT_DIR")"                       # infra/security
# CONFIG_FILE defaults to a *relative* path in _helpers.sh; pin it to this repo's
# config.yaml absolutely so cfg() resolves from any cwd — the bats suite runs this
# script from a temp dir, where the relative default ("infra/security/config.yaml")
# would miss and the script would hard-die on an advisory scan. An explicit
# absolute CONFIG_FILE from the environment is still honoured.
case "$CONFIG_FILE" in /*) ;; *) CONFIG_FILE="$SEC_DIR/config.yaml" ;; esac
SEC_OUTPUT_DIR="${1:-${SEC_OUTPUT_DIR:-}}"
resolve_sec_output_dir                                   # env → config → output/security
OUT_DIR="${SEC_OUTPUT_DIR%/}/osv"
# Merged SARIF — written empty here so it exists on EVERY path, including the
# SKIPPED exit below (a skip writes no scan SARIF of its own). The scan overwrites
# it with real content when it runs, so the code scanning upload always has a file in CI.
mkdir -p "$OUT_DIR"
merged_sarif="${OUT_DIR}/osv.sarif"
echo '{"version":"2.1.0","runs":[]}' > "$merged_sarif"
ROOT="${2:-$(cd "${SEC_DIR}/../.." && pwd)}"
OSV_BIN="${OSV_BIN:-osv-scanner}"                        # overridable: the test seam
TOOLS_INSTALLER="${TOOLS_INSTALLER:-${SEC_DIR}/tools-install.sh}"

lib_banner "scan-osv" "Source-level SCA + malicious (OSV-Scanner)"

# ---------- Tool: pin + verify (checksum), install if missing ----------------
# The pinned version is the single source of truth in config.yaml
# (.tools.osv-scanner) — the SAME flat key the CI job reads. Install through the
# repo's checksum-verifying installer, never curl|bash. Absent tool → install;
# present tool (e.g. the CI job pre-installed it) → used as-is.
osv_version="$(cfg '.tools.osv-scanner')"
[[ -n "$osv_version" ]] || die "no pinned OSV-Scanner version (add .tools.osv-scanner to ${CONFIG_FILE##*/})"
if ! command -v "$OSV_BIN" >/dev/null 2>&1; then
  warn "osv-scanner missing — installing ${osv_version} via tools-install.sh"
  # Install is best-effort, never a gate: a laptop with no sudo for /usr/local/bin, or
  # a CI image that did not pre-seed the tool, must not hard-die an advisory scan. Catch
  # the failure so it cannot trip the ERR trap; the presence check below then degrades to
  # an honest SKIPPED verdict instead of a false clean or a crash.
  run bash "$TOOLS_INSTALLER" osv-scanner "$osv_version" /usr/local/bin \
    || warn "osv-scanner install failed — the scan degrades to SKIPPED if the tool is still absent"
fi
# A real run must have the tool now. If it is still absent, degrade to SKIPPED (advisory:
# never gate, never falsely report clean) rather than dying at need_cmd.
if [[ "$DRY_RUN" != "true" ]] && ! command -v "$OSV_BIN" >/dev/null 2>&1; then
  _skipped_scan "$OUT_DIR" "OSV-Scanner" "osv-scanner ${osv_version} unavailable (not present and install failed)"
  exit 0
fi
need_cmd jq

# ---------- Scan (V2 CLI: `osv-scanner scan source [opts] [dir]`) ------------
# Offline snapshot: --download-offline-databases fetches the OSV DB when the
# cache is cold (robust on a fresh CI runner / first local run), then
# --offline-vulnerabilities matches against that local snapshot (deterministic,
# no live query). --allow-no-lockfiles keeps a repo with nothing OSV can read
# from failing this advisory scan. The node install + .git are excluded so a
# recursive scan of the repo tree does not pull in dev noise. OSV-Scanner writes
# one format per invocation, so the SARIF and JSON records are two passes over
# the same (cached) DB. A finding exits non-zero — capture it as the verdict,
# still write both reports, via an `if` so `set -e`/ERR does not abort first.
mkdir -p "$OUT_DIR"
sarif="${OUT_DIR}/osv.sarif.json"
json="${OUT_DIR}/report.json"
# OSV-Scanner renamed the exclude flag across v2 minor releases: v2.3.x exposes
# --experimental-exclude, v2.7.x renamed it to --x-exclude (keeping the old name as
# a deprecated alias). Probe which flag THIS binary understands so the scan works
# whether the box has an older version or the pinned one — never hard-crash on a
# flag the installed binary doesn't know ("flag provided but not defined: -x-exclude"
# on a laptop with a stale osv-scanner).
if "$OSV_BIN" scan source --help 2>&1 | grep -q -- '--x-exclude'; then
  exclude_flag=--x-exclude
else
  exclude_flag=--experimental-exclude
fi
scan_common=( --download-offline-databases --offline-vulnerabilities
              --allow-no-lockfiles -r
              "$exclude_flag" 'g:**/node_modules/**' "$exclude_flag" 'g:**/.git/**' )

if run "$OSV_BIN" scan source "${scan_common[@]}" -f sarif --output-file "$sarif" "$ROOT"; then
  osv_rc=0
else
  osv_rc=$?
fi

if run "$OSV_BIN" scan source "${scan_common[@]}" -f json --output-file "$json" "$ROOT"; then
  :
else
  :
fi

# ---------- Merge SARIF into one SARIF report (grype pattern) ------------
# Overwrite the empty Merged SARIF (written up-front, so it also exists when
# the scan is skipped) with the scan's SARIF when produced.
if [ -s "$sarif" ]; then
  jq -s '{version:"2.1.0", runs: map(.runs[])}' "$sarif" > "$merged_sarif"
fi

# ---------- Verdict (advisory) -----------------------------------------------
if [ "$osv_rc" -ne 0 ]; then
  warn "osv-scan reported findings (exit ${osv_rc}) — advisory, not blocking"
else
  success "osv-scan clean (exit 0)"
fi
if [[ -s "$json" ]]; then
  success "osv reports written: ${json}, ${merged_sarif}"
else
  warn "osv JSON record not written (scan did not run?); SARIF: ${merged_sarif}"
fi

# Advisory: a finding is a red, never a gate. Always exit 0.
exit 0
