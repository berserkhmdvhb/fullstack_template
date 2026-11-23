#!/usr/bin/env bash
# preview-cleanup.sh — remove pull request previews from the preview namespace (.github/workflows/preview.yml).
#
#   preview-cleanup.sh --pr N   remove the preview of pull request N (closed, or its `preview` label removed)
#   preview-cleanup.sh --sweep  remove every preview whose pull request is not open (OPEN_PRS) or whose
#                               last deploy is older than PREVIEW_TTL_DAYS
#
# A preview is the Helm release <SERVICE_NAME>-pr-<N> in <SERVICE_NAME>-preview (scripts/ci/deploy-env.sh,
# DEPLOY_ENV=preview). Removing one uninstalls the release, then deletes what Helm keeps: its migration
# Jobs (helm.sh/resource-policy keep) in the preview namespace and its PostgreSQL volume (a StatefulSet's
# claim) in the preview database namespace, both by the release's app.kubernetes.io/instance label.
# It runs as the preview deployer, whose rights end at those two namespaces (infra/k8s/bootstrap), and it
# refuses any other namespace and any release name that is not <SERVICE_NAME>-pr-<N>.
#
# Env: SERVICE_NAME (required); NAMESPACE (optional, and then it must be <SERVICE_NAME>-preview);
# OPEN_PRS (--sweep, required: the open pull request numbers, space or comma separated; empty means
# none is open); PREVIEW_TTL_DAYS (7); HELM_TIMEOUT (5m); DB_SUFFIX (db, the chart's namespaces.dbSuffix).
set -Eeuo pipefail

fail() { echo "::error::$*" >&2; exit 1; }
usage() { sed -n '3,4p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2; exit 2; }

[[ -n "${SERVICE_NAME:-}" ]] || fail "SERVICE_NAME is required"
expected="${SERVICE_NAME}-preview"
NAMESPACE="${NAMESPACE:-$expected}"
[[ "$NAMESPACE" == "$expected" ]] || fail "refusing namespace ${NAMESPACE}: previews live in ${expected} only"
DB_NAMESPACE="${NAMESPACE}-${DB_SUFFIX:-db}"
HELM_TIMEOUT="${HELM_TIMEOUT:-5m}"
TTL_DAYS="${PREVIEW_TTL_DAYS:-7}"
[[ "$TTL_DAYS" =~ ^[0-9]+$ ]] || fail "PREVIEW_TTL_DAYS must be a whole number of days"
pattern="^${SERVICE_NAME//./\\.}-pr-([1-9][0-9]{0,9})$"

remove() {
  local release="$1"
  [[ "$release" =~ $pattern ]] || fail "refusing ${release}: not a preview release (${SERVICE_NAME}-pr-<N>)"
  local out rc=0
  out="$(helm status "$release" -n "$NAMESPACE" 2>&1)" || rc=$?
  if (( rc == 0 )); then
    helm uninstall "$release" -n "$NAMESPACE" --wait --timeout "$HELM_TIMEOUT"
  elif grep -qi "not found" <<<"$out"; then
    echo "${release}: no release in ${NAMESPACE}"
  else
    fail "cannot read the release ${release} in ${NAMESPACE}: $(tail -n 1 <<<"$out")"
  fi
  kubectl -n "$NAMESPACE" delete jobs -l "app.kubernetes.io/instance=${release}" --ignore-not-found
  kubectl -n "$DB_NAMESPACE" delete pvc -l "app.kubernetes.io/instance=${release}" --ignore-not-found
  echo "Removed the preview ${release}"
  echo "- Removed the preview \`${release}\`" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
}

[[ $# -ge 1 ]] || usage
case "$1" in
  --pr)
    [[ $# -eq 2 && "$2" =~ ^[1-9][0-9]{0,9}$ ]] || fail "--pr needs a pull request number"
    remove "${SERVICE_NAME}-pr-$2" ;;
  --sweep)
    [[ $# -eq 1 ]] || usage
    [[ -n "${OPEN_PRS+set}" ]] || fail "--sweep needs OPEN_PRS (the open pull request numbers; empty for none)"
    open=" ${OPEN_PRS//,/ } "
    now="$(date -u +%s)"
    # Every release whatever its state (a stuck pending one too), its last deploy time as RFC 3339.
    releases="$(helm list -n "$NAMESPACE" --deployed --failed --pending --uninstalling \
      --time-format "2006-01-02T15:04:05Z07:00" -o json)"
    while IFS=$'\t' read -r release updated; do
      [[ -n "$release" ]] || continue
      if [[ ! "$release" =~ $pattern ]]; then
        echo "${release}: not a preview release, left alone"
        continue
      fi
      pr="${BASH_REMATCH[1]}"
      age_days=$(( (now - $(date -u -d "$updated" +%s)) / 86400 ))
      if [[ "$open" != *" ${pr} "* ]]; then
        echo "${release}: pull request ${pr} is not open"
        remove "$release"
      elif (( age_days >= TTL_DAYS )); then
        echo "${release}: last deployed ${age_days} days ago (PREVIEW_TTL_DAYS ${TTL_DAYS})"
        remove "$release"
      else
        echo "${release}: kept (pull request ${pr} is open, last deployed ${age_days} days ago)"
      fi
    done < <(jq -r '.[] | [.name, .updated] | @tsv' <<<"$releases")
    ;;
  *) usage ;;
esac
