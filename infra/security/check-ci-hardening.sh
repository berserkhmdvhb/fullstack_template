#!/usr/bin/env bash
# check-ci-hardening.sh — CI's-own-supply-chain hardening self-check (standard 04 §6.4).
#
# Statically lints THIS repo's GitHub workflows and composite actions (.github/workflows/*.yml,
# .github/actions/*/action.yml) against the table-stakes: actions pinned to a commit SHA, top-level
# permissions, no pull_request_target, no inline secrets, digest-pinned images, environment-scoped
# secrets, job timeouts and an osv-scanner job. SLSA provenance surfaces as SKIP until phase 2
# (never a false GREEN, never a gate-blocking FAIL). Exits non-zero (RED) if any finding is reported.
# The grading logic lives in ci_hardening_lint.py; this is the thin wrapper the
# CI job runs. Source-level (reads committed YAML, no build / image), so it runs
# on pull requests for fast feedback AND on protected refs.
#
#   usage: check-ci-hardening.sh [REPO_ROOT] [SEC_OUTPUT_DIR]
# NOTE: intentionally `set -uo pipefail` with NO `-e` and NO ERR trap — a RED
# verdict (the linter exiting 1) is a real result we must propagate, not a script
# error to abort on; aborting would skip writing the JSON report below.
set -uo pipefail

# ---------- Shared runtime ----------
# Only the logging / guard helpers are used here (no trap_err: see the note above).
# shellcheck source=/dev/null
source "$(dirname "$0")/_helpers.sh"

# ---------- Usage ----------
usage() {
  cat <<'USAGE'
Usage: check-ci-hardening.sh [REPO_ROOT] [SEC_OUTPUT_DIR]

Statically lint this repo's GitHub workflows and actions against the supply-chain
table-stakes (SHA-pinned actions, permissions, digest-pinned images, environment-scoped
secrets, timeouts, osv coverage). SLSA provenance is SKIP until phase 2. Exit non-zero
(RED) if any check reports a finding.

  REPO_ROOT       repo root            (default: this script's ../..)
  SEC_OUTPUT_DIR  security output dir  (default: output/security)
USAGE
  exit 0
}

# --help short-circuit (usage() is defined above).
case "${1:-}" in -h|--help) usage ;; esac

# ---------- Defaults ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${1:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
SEC_OUTPUT_DIR="${2:-${SEC_OUTPUT_DIR:-output/security}}"
OUT_DIR="${SEC_OUTPUT_DIR}/ci-hardening"
OUT="${OUT_DIR}/report.json"

lib_banner "check-ci-hardening" "CI's-own supply-chain hardening self-check"

# ---------- Pre-flight ----------
need_cmd python3
[[ -f "${SCRIPT_DIR}/ci_hardening_lint.py" ]] || die "ci_hardening_lint.py not found: ${SCRIPT_DIR}/ci_hardening_lint.py"
mkdir -p "${OUT_DIR}"

log "Linting committed workflows in: ${ROOT}"

python3 "${SCRIPT_DIR}/ci_hardening_lint.py" "${ROOT}"; rc=$?
# Write the machine-readable report. The linter exits non-zero on a RED gate,
# which is a *verdict* we propagate via $rc below (see the header note) — not a
# write error. So only a genuine linter failure (exit >=2) or a missing/empty
# report file is fatal here; a RED verdict (exit 1) must still write and pass.
python3 "${SCRIPT_DIR}/ci_hardening_lint.py" --json "${ROOT}" > "${OUT}"; json_rc=$?
{ [ "${json_rc}" -le 1 ] && [ -s "${OUT}" ]; } || die "failed to write report: ${OUT} (linter exit ${json_rc})"

success "ci-hardening report written: ${OUT}"

if [ "${rc}" -ne 0 ]; then
  error "CI hardening gate RED — one or more workflow checks reported a finding"
fi
exit "${rc}"
