#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# scan-iac.sh — Infrastructure-as-Code misconfiguration scan with Checkov.
#
# Checkov (bridgecrewio/checkov) is the IaC *policy* layer: it scans Terraform,
# CloudFormation and raw Kubernetes manifests against a catalog of security
# best-practice checks (the CIS benchmarks, and the NSA-CISA hardening guidance
# that Kubescape enforces at the workload level — see
# wiki/supply-chain-tooling-assessment.md for the Checkov<->Kubescape division).
# It is pip-only (integrity via the pinned wheel + PyPI's hash, NOT a release
# checksum), so it lives outside the checksum-verified .tools map — installed
# inline here, the pip_audit / GuardDog pattern.
#
# This is the SINGLE SOURCE OF TRUTH for the IaC scan: the CI `iac_scan` job and
# the local `devsec.sh iac` command both call it, so CI and a laptop never drift.
#
# Advisory (exit 0): an IaC misconfiguration is reported, not a hard gate — the
# CI job is allow_failure: true. A non-zero scan exit is captured as the verdict
# and the reports are still written first.
#
# Scope note — Helm charts: Checkov reads Terraform / CloudFormation / raw K8s
# YAML directly. It does NOT render Helm `{{ .Values.* }}` templates (its own
# `helm` framework no-ops in this environment, and scanning the rendered manifests
# with the `kubernetes` framework aborts — Checkov 3.2.x segfaults on the
# Deployment/StatefulSet manifests every real chart emits). So a repo whose only
# IaC is a Helm chart yields 0 scannable resources: this step reports that as an
# honest SKIPPED (never a false GREEN), and the workload posture is covered by
# Kubescape (the k8s_scan job), which parses Helm charts natively.
#
# Usage: scan-iac.sh [SEC_OUTPUT_DIR] [PROJECT_ROOT]
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
Usage: scan-iac.sh [SEC_OUTPUT_DIR] [PROJECT_ROOT]

IaC misconfiguration scan (Checkov) over Terraform / CloudFormation / raw K8s.
Helm charts are not rendered here (Checkov can't extract resources from them);
the workload is scanned by Kubescape (devsec.sh k8s).
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
OUT_DIR="${SEC_OUTPUT_DIR%/}/iac"
# Merged SARIF — written empty here so it exists on EVERY path, including the
# SKIPPED exits below (a skip writes no scan SARIF of its own). The scan overwrites
# it with real content when it runs, so the code scanning upload always has a file in CI.
mkdir -p "$OUT_DIR"
merged_sarif="${OUT_DIR}/iac.sarif"
echo '{"version":"2.1.0","runs":[]}' > "$merged_sarif"
ROOT="${2:-$(cd "${SEC_DIR}/../.." && pwd)}"
CHECKOV_BIN="${CHECKOV_BIN:-checkov}"                    # overridable: the test seam
CARGO_BIN="${CARGO_BIN:-cargo}"                          # overridable: the test seam
RUSTC_BIN="${RUSTC_BIN:-rustc}"                          # overridable: the test seam
# The IaC surfaces Checkov scans (array, so the words split into separate argv —
# not a bare string, which would trip SC2086). `kubernetes` is Checkov's accepted
# framework token (the old `k8s` alias was dropped in Checkov 3.x — passing it
# aborts with "Invalid frameworks specified: k8s").
CK_FRAMEWORKS=(terraform cloudformation kubernetes helm)
# Never scan vendored third-party deps or VCS metadata: not the repo's IaC and only
# adds noise. Mirrors the node_modules/.git exclusion in the has_iac detection above.
CK_SKIP=(--skip-path node_modules --skip-path .git)

lib_banner "scan-iac" "IaC misconfiguration scan (Checkov)"

need_cmd jq

# ---------- Tool: pin + install if missing (the pip_audit / GuardDog pattern) --
# The pin is the single source of truth in config.yaml (.tools_pip.checkov).
# Checkov is pip-only (integrity via the pinned wheel + PyPI's hash), so it lives
# outside the checksum-verified .tools map — install it here if absent.
ck_version="$(cfg '.tools_pip.checkov')"
[[ -n "$ck_version" ]] || die "no pinned Checkov version (add .tools_pip.checkov to ${CONFIG_FILE##*/})"

if ! command -v "$CHECKOV_BIN" >/dev/null 2>&1; then
  warn "checkov missing — installing ${ck_version} via pip"
  # Install is best-effort, never a gate: a laptop with no pip / no network, or a CI
  # image that did not pre-seed the tool, must not hard-die an advisory scan. Catch the
  # failure so it cannot trip the ERR trap; the presence check below then degrades to an
  # honest SKIPPED verdict instead of a false clean or a crash.
  #
  # Checkov pulls in `rustworkx`, which has no source-free install: when no wheel matches
  # this Python it builds from source, which needs a Rust toolchain — the slow, doomed
  # compile seen on very new CPython (e.g. 3.14). Without a toolchain present, take only
  # prebuilt wheels (--only-binary=:all:): on a no-Rust host that is the SAME verdict as a
  # plain install (both succeed iff every dependency has a wheel) — the CI runner's Python
  # 3.12 has the wheel and installs cleanly — just fast instead of sitting through a build
  # that cannot succeed. With a toolchain present, allow the source build so a laptop that
  # installs Rust can still install from source.
  if command -v "$CARGO_BIN" >/dev/null 2>&1 || command -v "$RUSTC_BIN" >/dev/null 2>&1; then
    run pip install --no-cache-dir --disable-pip-version-check "checkov==${ck_version}" \
      || warn "checkov install failed — the scan degrades to SKIPPED if the tool is still absent"
  else
    run pip install --no-cache-dir --disable-pip-version-check --only-binary=:all: "checkov==${ck_version}" \
      || warn "checkov install failed (no Rust toolchain for the rustworkx dep, no matching wheel) — the scan degrades to SKIPPED"
  fi
fi
# A real run must have the tool now. If it is still absent, degrade to SKIPPED (advisory:
# never gate, never falsely report clean) rather than dying at need_cmd.
if [[ "$DRY_RUN" != "true" ]] && ! command -v "$CHECKOV_BIN" >/dev/null 2>&1; then
  _skipped_scan "$OUT_DIR" "Checkov" "checkov ${ck_version} unavailable (not present and install failed)"
  exit 0
fi

# ---------- Enumerate inputs (IaC present?) ----------------------------------
# Checkov is an IaC scanner; a repo with no IaC manifests has nothing for it to
# read. Detect that up front (cheap: a find + a grep) and degrade to an honest
# SKIPPED — the *current* template state — rather than running an empty scan or
# falsely reporting GREEN. Excludes node_modules + .git so a recursive scan of the
# repo tree does not pull in third-party noise. `Chart.yaml` counts as IaC so a
# Helm-only repo reaches the scan (and is then reported SKIPPED for 0 resources,
# not silently — see the verdict), since Kubescape owns the chart workload scan.
has_iac=0
# Each test reads a captured value, never a pipe: a reader that stops early could kill a still-
# writing find with SIGPIPE under pipefail. The first find stops at its first hit (-print -quit);
# the fallback greps the files in batches (-exec ... {} +) and its whole output is captured.
if [[ -n "$(find "$ROOT" -type f \( -name '*.tf' -o -name '*.tf.json' -o -name 'Chart.yaml' \) \
       -not -path '*/node_modules/*' -not -path '*/.git/*' -print -quit 2>/dev/null)" ]]; then
  has_iac=1
elif [[ -n "$(find "$ROOT" -type f -not -path '*/node_modules/*' -not -path '*/.git/*' \
       -exec grep -lE '^(kind:|AWSTemplateFormatVersion:|[[:space:]]+Resources:)' {} + 2>/dev/null)" ]]; then
  has_iac=1
fi
if [[ "$has_iac" -eq 0 ]]; then
  _skipped_scan "$OUT_DIR" "Checkov" "no IaC/Helm/K8s manifests found under ${ROOT}"
  exit 0
fi

# ---------- Scan (directory scan, multi-framework) ---------------------------
# One invocation writes both the SARIF and the JSON. A misconfiguration exits
# non-zero — capture it as the verdict, still write both reports, via an `if` so
# `set -e`/ERR does not abort first. Checkov can also ABORT (e.g. segfault on some
# K8s manifests) and write no output at all; that is captured the same way (a
# non-zero exit) and, since the JSON is then absent/empty, the verdict below reads
# it as 0 resources — an advisory scan must never hard-die on a tool crash.
mkdir -p "$OUT_DIR"
sarif="${OUT_DIR}/iac.sarif.json"
ck_json="${OUT_DIR}/checkov.json"
report="${OUT_DIR}/report.json"
_show_run "$CHECKOV_BIN" -d "$ROOT" --framework "${CK_FRAMEWORKS[@]}" \
  "${CK_SKIP[@]}" -o sarif --output-file-path "$sarif" -o json --output-file-path "$ck_json"
if run "$CHECKOV_BIN" -d "$ROOT" --framework "${CK_FRAMEWORKS[@]}" \
      "${CK_SKIP[@]}" -o sarif --output-file-path "$sarif" -o json --output-file-path "$ck_json"; then
  ck_rc=0
else
  ck_rc=$?
fi

# ---------- Merge SARIF into one SARIF report (grype/osv pattern) --------
# Overwrite the empty Merged SARIF (written up-front, so it also exists on the
# SKIPPED paths) with the scan's SARIF when the scan produced one.
if [ -s "$sarif" ]; then
  jq -s '{version:"2.1.0", runs: map(.runs[])}' "$sarif" > "$merged_sarif"
fi

# ---------- Verdict (advisory) -----------------------------------------------
# Read the record defensively: a crashed/absent scan leaves no (valid) JSON, which
# must read as "0 resources" so we report an honest SKIPPED, never a crash and
# never a false GREEN.
resource_count=0
failed_count=0
if [ -s "$ck_json" ] && jq -e . "$ck_json" >/dev/null 2>&1; then
  resource_count="$(jq '.summary.resource_count // 0' "$ck_json")"
  failed_count="$(jq '.summary.failed // 0' "$ck_json")"
fi
# Guard against a non-integer leaking out of the defensive read above.
[[ "$resource_count" =~ ^[0-9]+$ ]] || resource_count=0
[[ "$failed_count" =~ ^[0-9]+$ ]] || failed_count=0

if [ "$resource_count" -eq 0 ]; then
  # has_iac was true (a Chart.yaml / TF / raw-K8s marker exists) but Checkov
  # extracted 0 scannable resources — the false-clean we are fixing. The common
  # cause is a Helm-only repo: Checkov does not render the templates. Report it
  # honestly (SKIPPED) and point at the tool that does cover the workload.
  status="SKIPPED"
  warn "SKIPPED: Checkov found IaC/Helm manifests under ${ROOT} but extracted 0 scannable resources (it does not render Helm templates, and no raw Terraform/CloudFormation/K8s was present) — nothing to check here; the workload posture is scanned by Kubescape (devsec.sh k8s)"
elif [ "$ck_rc" -ne 0 ] || [ "$failed_count" -gt 0 ]; then
  status="RED"
  warn "checkov reported ${failed_count} misconfiguration(s) (exit ${ck_rc}) — advisory, not blocking"
else
  status="GREEN"
  success "iac-scan clean: ${resource_count} resource(s) scanned (exit 0)"
fi

jq -n \
  --arg tool "Checkov" \
  --arg frameworks "${CK_FRAMEWORKS[*]}" \
  --arg status "$status" \
  --argjson rc "$ck_rc" \
  --argjson resources "$resource_count" \
  '{tool:$tool, framework:$frameworks, exit_code:$rc, resources_scanned:$resources, status:$status}' \
  > "$report"
success "iac reports written: ${report}, ${merged_sarif}"

# Advisory: a misconfiguration is a red, never a gate. Always exit 0.
exit 0
