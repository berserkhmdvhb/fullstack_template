#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# scan-k8s.sh — Kubernetes workload + posture scan with Kubescape.
#
# Kubescape (kubescape/kubescape) is the K8s *workload hardening* layer: it scans
# manifests and Helm charts against the NSA-CISA and MITRE ATT&CK frameworks (and
# their controls) — the posture complement to Checkov, which owns IaC *policy*
# over the templates (see wiki/supply-chain-tooling-assessment.md for the
# Checkov<->Kubescape division + the one deliberate overlap). Kubescape is a Go
# static binary, so it installs via the checksum-verifying tools-install.sh (a
# new `kubescape` case there — the one sanctioned edit to a frozen script).
#
# CI has no cluster, so this is an offline *workload* scan over the Helm charts +
# K8s manifests in the repo (`scan framework … <dir>`, path positional), NOT
# `scan cluster`.
#
# This is the SINGLE SOURCE OF TRUTH for the K8s scan: the CI `k8s_scan` job and
# the local `devsec.sh k8s` command both call it.
#
# Advisory (exit 0): a workload misconfiguration is reported, not a hard gate —
# the CI job is allow_failure: true. A non-zero scan exit is captured as the
# verdict and the reports are still written first.
#
# Usage: scan-k8s.sh [SEC_OUTPUT_DIR] [PROJECT_ROOT]
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
Usage: scan-k8s.sh [SEC_OUTPUT_DIR] [PROJECT_ROOT]

Kubernetes workload/posture scan (Kubescape) against NSA-CISA + MITRE frameworks.
Advisory: a misconfiguration is reported, not a hard gate (exit 0).

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
OUT_DIR="${SEC_OUTPUT_DIR%/}/k8s"
# Merged SARIF — written empty here so it exists on EVERY path, including the
# SKIPPED exits below (a skip writes no scan SARIF of its own). The scan overwrites
# it with real content when it runs, so the code scanning upload always has a file in CI.
mkdir -p "$OUT_DIR"
merged_sarif="${OUT_DIR}/k8s.sarif"
echo '{"version":"2.1.0","runs":[]}' > "$merged_sarif"
ROOT="${2:-$(cd "${SEC_DIR}/../.." && pwd)}"
KUBESCAPE_BIN="${KUBESCAPE_BIN:-kubescape}"              # overridable: the test seam
KUBESCAPE_FRAMEWORKS="nsa,mitre"                         # NSA-CISA + MITRE ATT&CK
# Verbose passthrough (off by default): KUBESCAPE_VERBOSE=1 appends Kubescape's -v,
# which prints the per-resource control failures the summary otherwise hides
# ("Run with --verbose/-v to see control failures for each resource"). An array, so
# it is a separate argv word when on and nothing when off — the invocation stays
# byte-identical to a plain run unless the flag is set. Honoured in both the
# _show_run banner and the real run, so what the banner previews is what runs.
if [[ "${KUBESCAPE_VERBOSE:-0}" == "1" ]]; then ks_verbose=(-v); else ks_verbose=(); fi
# Never scan vendored third-party deps or VCS metadata: they are not the repo's
# workload and only add parse noise (e.g. frontend/node_modules tsconfig files).
# Mirrors the node_modules/.git exclusion already used by the has_k8s detection above.
KUBESCAPE_EXCLUDE=(--exclude-path node_modules --exclude-path .git)
TOOLS_INSTALLER="${TOOLS_INSTALLER:-${SEC_DIR}/tools-install.sh}"

lib_banner "scan-k8s" "Kubernetes workload/posture scan (Kubescape)"

need_cmd jq

# ---------- Tool: pin + verify (checksum), install if missing ---------------
# The pin is the single source of truth in config.yaml (.tools.kubescape) — the
# SAME flat key the CI job reads. Install through the repo's checksum-verifying
# installer, never curl|bash. Absent tool → install; present tool (e.g. the CI job
# pre-installed it) → used as-is.
ks_version="$(cfg '.tools.kubescape')"
[[ -n "$ks_version" ]] || die "no pinned Kubescape version (add .tools.kubescape to ${CONFIG_FILE##*/})"
if ! command -v "$KUBESCAPE_BIN" >/dev/null 2>&1; then
  # Install is best-effort, never a gate: a laptop with no sudo for /usr/local/bin, or
  # a CI image that did not pre-seed the tool, must not hard-die an advisory scan. Catch
  # the failure so it cannot trip the ERR trap; the presence check below then degrades to
  # an honest SKIPPED verdict instead of a false clean or a crash.
  #
  # tools-install.sh fetches the release with `curl` (frozen: it is a standalone `run
  # curl`, so a missing curl fires the ERR trap's "ERROR @" even though we catch the
  # outer failure — a false hard-die). So only attempt the install when a fetch tool is
  # present; with none, skip the attempt and let the presence check degrade to SKIPPED.
  if command -v curl >/dev/null 2>&1; then
    warn "kubescape missing — installing ${ks_version} via tools-install.sh"
    run bash "$TOOLS_INSTALLER" kubescape "$ks_version" /usr/local/bin \
      || warn "kubescape install failed — the scan degrades to SKIPPED if the tool is still absent"
  else
    warn "kubescape missing — cannot install ${ks_version} (no curl to fetch the release) — the scan degrades to SKIPPED"
  fi
fi
# A real run must have the tool now. If it is still absent, degrade to SKIPPED (advisory:
# never gate, never falsely report clean) rather than dying at need_cmd.
if [[ "$DRY_RUN" != "true" ]] && ! command -v "$KUBESCAPE_BIN" >/dev/null 2>&1; then
  _skipped_scan "$OUT_DIR" "Kubescape" "kubescape ${ks_version} unavailable (not present and install failed)"
  exit 0
fi

# ---------- Enumerate inputs (K8s / Helm present?) ---------------------------
# Kubescape is a K8s/Helm scanner; a repo with no manifests has nothing for it to
# read. Detect that up front (cheap: a find + a grep) and degrade to an honest
# SKIPPED — the *current* template state — rather than running an empty scan or
# falsely reporting GREEN. Excludes node_modules + .git.
has_k8s=0
# Each test reads a captured value, never a pipe: a reader that stops early could kill a still-
# writing find with SIGPIPE under pipefail. The first find stops at its first hit (-print -quit);
# the fallback greps the files in batches (-exec ... {} +) and its whole output is captured.
if [[ -n "$(find "$ROOT" -type f \( -name 'Chart.yaml' -o -name 'kustomization.yaml' -o -name 'kustomization.yml' \) \
       -not -path '*/node_modules/*' -not -path '*/.git/*' -print -quit 2>/dev/null)" ]]; then
  has_k8s=1
elif [[ -n "$(find "$ROOT" -type f -not -path '*/node_modules/*' -not -path '*/.git/*' \
       -exec grep -lE '^kind:' {} + 2>/dev/null)" ]]; then
  has_k8s=1
fi
if [[ "$has_k8s" -eq 0 ]]; then
  _skipped_scan "$OUT_DIR" "Kubescape" "no K8s/Helm manifests found under ${ROOT}"
  exit 0
fi

# ---------- Scan (offline workload scan, NSA + MITRE frameworks) -------------
# One invocation writes both the SARIF and the JSON. A misconfiguration exits
# non-zero — capture it as the verdict, still write both reports, via an `if` so
# `set -e`/ERR does not abort first.
mkdir -p "$OUT_DIR"
# Kubescape v4 writes one file per --format, appending the extension to the single
# --output base (e.g. `--format sarif,json --output base` -> base.sarif + base.json),
# and takes the scan path positionally — it dropped the `--input <dir>` flag the old
# invocation used. So `k8s.sarif.json`/`kubescape.json` become `kubescape.sarif`/
# `kubescape.json`; the merged k8s.sarif and report.json are unchanged, so
# CI's SARIF upload path is unaffected.
base="${OUT_DIR}/kubescape"
sarif="${base}.sarif"          # v4 appends .sarif / .json to the --output base
report="${OUT_DIR}/report.json"
_show_run "$KUBESCAPE_BIN" scan framework "$KUBESCAPE_FRAMEWORKS" "$ROOT" \
  "${ks_verbose[@]}" "${KUBESCAPE_EXCLUDE[@]}" --format sarif,json --output "$base"
if run "$KUBESCAPE_BIN" scan framework "$KUBESCAPE_FRAMEWORKS" "$ROOT" \
      "${ks_verbose[@]}" "${KUBESCAPE_EXCLUDE[@]}" --format sarif,json --output "$base"; then
  ks_rc=0
else
  ks_rc=$?
fi

# ---------- Merge SARIF into one SARIF report (grype/osv pattern) --------
# Overwrite the empty Merged SARIF (written up-front, so it also exists on the
# SKIPPED paths) with the scan's SARIF when the scan produced one.
if [ -s "$sarif" ]; then
  jq -s '{version:"2.1.0", runs: map(.runs[])}' "$sarif" > "$merged_sarif"
fi

# ---------- Verdict (advisory) -----------------------------------------------
if [ "$ks_rc" -ne 0 ]; then
  status="RED"
  warn "kubescape reported misconfigurations (exit ${ks_rc}) — advisory, not blocking"
else
  status="GREEN"
  success "k8s-scan clean (exit 0)"
fi
jq -n --arg tool "Kubescape" --arg frameworks "$KUBESCAPE_FRAMEWORKS" \
      --arg status "$status" --argjson rc "$ks_rc" \
  '{tool:$tool, framework:$frameworks, exit_code:$rc, status:$status}' > "$report"
success "k8s reports written: ${report}, ${merged_sarif}"

# Advisory: a misconfiguration is a red, never a gate. Always exit 0.
exit 0
