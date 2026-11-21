#!/usr/bin/env bash
# migration-job-lib.sh — source me. Look up and wait for a release's migration Job (kubectl only).
#
# The chart creates one Job per release revision (<fullname>-migrate-r<N>, kept up to 24 hours), so the
# one that belongs to a deploy is the NEWEST Job of THIS release: the component label picks migration
# Jobs and the instance label scopes them to the release, so another release's Job is never chosen.
#
#   migration_job_name   NAMESPACE RELEASE            name of the newest Job (empty when none)
#   migration_job_status NAMESPACE RELEASE            "name|succeeded|failed|FailedCondition" of it
#   migration_job_wait   NAMESPACE RELEASE SECONDS    0 Complete, 1 Failed, 2 not finished or none
#   migration_job_logs   NAMESPACE RELEASE [LINES]    bounded log tail of its Pods (default 50)
#   migration_job_report NAMESPACE RELEASE            status line, then the logs (for a failed deploy)
# MIGRATION_POLL_SLEEP (3) is the seconds between polls in migration_job_wait.

_migration_job_list() { # NAMESPACE RELEASE JSONPATH
  kubectl -n "$1" get job \
    -l "app.kubernetes.io/component=migration,app.kubernetes.io/instance=$2" \
    --sort-by=.metadata.creationTimestamp -o jsonpath="$3" | tail -n 1
}

migration_job_name() {
  _migration_job_list "$1" "$2" '{range .items[*]}{.metadata.name}{"\n"}{end}'
}

migration_job_status() {
  _migration_job_list "$1" "$2" \
    '{range .items[*]}{.metadata.name}{"|"}{.status.succeeded}{"|"}{.status.failed}{"|"}{.status.conditions[?(@.type=="Failed")].status}{"\n"}{end}'
}

migration_job_wait() {
  local ns="$1" release="$2" seconds="$3" row name ok failed cond waited=0
  while :; do
    row="$(migration_job_status "$ns" "$release")"
    if [[ -n "$row" ]]; then
      IFS='|' read -r name ok failed cond <<<"$row"
      if [[ "$ok" == "1" ]]; then echo "migration job $name completed"; return 0; fi
      if [[ "$cond" == "True" ]]; then echo "migration job $name failed (failed pods: ${failed:-0})"; return 1; fi
    fi
    if (( waited >= seconds )); then
      if [[ -z "$row" ]]; then echo "no migration job found for release $release in $ns"
      else echo "migration job ${name:-?} did not finish within ${seconds}s"; fi
      return 2
    fi
    sleep "${MIGRATION_POLL_SLEEP:-3}"
    waited=$((waited + ${MIGRATION_POLL_SLEEP:-3}))
  done
}

migration_job_logs() {
  local name
  name="$(migration_job_name "$1" "$2")"
  [[ -n "$name" ]] || return 0
  kubectl -n "$1" logs -l "job-name=$name" --tail "${3:-50}" --all-containers=true --ignore-errors=true 2>&1 || true
}

migration_job_report() {
  local row name
  row="$(migration_job_status "$1" "$2")"
  if [[ -z "$row" ]]; then echo "migration job: none found for release $2 in $1"; return 0; fi
  name="${row%%|*}"
  echo "migration job: $row  (name|succeeded|failed|FailedCondition)"
  kubectl -n "$1" get job "$name" -o wide 2>&1 || true
  echo "migration job logs (last 50 lines per Pod):"
  migration_job_logs "$1" "$2" 50
}
