#!/usr/bin/env bash
# lighthouse-assert.sh — check the Lighthouse CI budgets (advisory; backs the lighthouse job in reusable-frontend.yml).
#
#   scripts/ci/lighthouse-assert.sh [CONFIG]    run from frontend/ after `lhci collect`; CONFIG defaults to lighthouserc.json
#
# `lhci assert` exits 1 both for a failed budget and for a tool error. A failed budget leaves failed entries in
# .lighthouseci/assertion-results.json: those become ::warning annotations and a step-summary line, and the script
# exits 0. Anything else (no results file, nothing failed) is a tool error: the script exits with lhci's code.
set -euo pipefail

config="${1:-lighthouserc.json}"
results=.lighthouseci/assertion-results.json
summary="${GITHUB_STEP_SUMMARY:-/dev/null}"

rm -f "$results"
code=0
npx lhci assert --config="$config" || code=$?

if (( code == 0 )); then
  echo 'Lighthouse CI: all budgets met' >> "$summary"
  exit 0
fi

if [[ -f "$results" ]] && jq -e 'any(.[]; .passed == false)' "$results" >/dev/null; then
  jq -r '.[] | select(.passed == false)
    | "::warning title=Lighthouse (advisory)::\(.url) \(.auditId) \(.auditProperty // "") \(.name): expected \(.operator) \(.expected), found \(.actual)"' "$results"
  echo "Lighthouse CI (advisory): $(jq '[.[] | select(.passed == false)] | length' "$results") budget finding(s); see the lighthouse-reports artifact" >> "$summary"
  exit 0
fi

echo "lighthouse-assert: lhci assert failed (exit $code) without a budget finding: a tool error" >&2
exit "$code"
