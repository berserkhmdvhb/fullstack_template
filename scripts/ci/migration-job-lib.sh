#!/usr/bin/env bash
# migration-job-lib.sh — source me. Look up and wait for a release's migration Job (helm + kubectl).
#
# The chart creates one Job per release revision (<fullname>-migrate-r<N>, kept up to 24 hours), so
# "the" migration Job is the one NAMED IN THE RELEASE'S CURRENT MANIFEST (`helm get manifest`, the Job
# labelled component=migration), never "the newest one in the namespace": after a manual rollback the
# newest kept Job can be a Failed one from the revision that was rolled back.
#
# Convention: on success stdout holds the result; on failure stdout holds a one-line message and the
# status is non-zero, so a caller under `set -e` writes `out="$(f ...)" || rc=$?` and is never aborted.
# Status: 0 ok, 3 no such release, 4 helm or kubectl failed, 5 the manifest has no migration Job;
# migration_job_wait adds 1 (Job Failed) and 2 (not finished, or no Job object).
#
#   migration_job_name   NAMESPACE RELEASE            the manifest's migration Job name
#   migration_job_status NAMESPACE RELEASE            "name|succeeded|failed|FailedCondition" (empty: Job gone, e.g. TTL)
#   migration_job_wait   NAMESPACE RELEASE SECONDS    waits for Complete or Failed
#   migration_job_logs   NAMESPACE RELEASE [LINES]    bounded log tail of its Pods (default 50)
#   migration_job_report NAMESPACE RELEASE            status line and logs of the manifest's Job; always returns 0
#   migration_job_report_newest NAMESPACE RELEASE     the same for the NEWEST Job of the release (after a failed deploy was rolled back)
#   migration_job_purge_failed NAMESPACE RELEASE      delete Failed migration Jobs of THIS release (first install)
# MIGRATION_POLL_SLEEP (3) is the seconds between polls in migration_job_wait.

migration_job_name() {
  local ns="$1" release="$2" out rc=0
  out="$(helm get manifest "$release" -n "$ns" 2>&1)" || rc=$?
  if (( rc != 0 )); then
    if grep -qi "not found" <<<"$out"; then
      echo "no release $release in $ns"; return 3
    fi
    echo "helm get manifest failed: $(tail -n 1 <<<"$out")"; return 4
  fi
  local name
  name="$(awk '
    function flush() { if (kind == "Job" && mig && name != "") found = name; kind = ""; mig = 0; name = "" }
    /^---/ { flush(); next }
    /^kind: / { kind = $2 }
    /^  name: / && name == "" { name = $2 }
    /^    app.kubernetes.io\/component: migration/ { mig = 1 }
    END { flush(); if (found != "") print found }' <<<"$out")"
  if [[ -z "$name" ]]; then
    echo "the manifest of release $release has no migration Job"; return 5
  fi
  echo "$name"
}

migration_job_status() {
  local ns="$1" release="$2" name out rc=0
  name="$(migration_job_name "$ns" "$release")" || { rc=$?; echo "$name"; return "$rc"; }
  out="$(kubectl -n "$ns" get job "$name" -o jsonpath='{.metadata.name}{"|"}{.status.succeeded}{"|"}{.status.failed}{"|"}{.status.conditions[?(@.type=="Failed")].status}' 2>&1)" || rc=$?
  if (( rc != 0 )); then
    if grep -qi "notfound\|not found" <<<"$out"; then return 0; fi
    echo "kubectl get job $name failed: $(tail -n 1 <<<"$out")"; return 4
  fi
  echo "$out"
}

migration_job_wait() {
  local ns="$1" release="$2" seconds="$3" row name ok failed cond waited=0 rc
  while :; do
    rc=0
    row="$(migration_job_status "$ns" "$release")" || rc=$?
    if (( rc != 0 )); then echo "$row"; return "$rc"; fi
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
  name="$(migration_job_name "$1" "$2")" || { echo "$name"; return 0; }
  kubectl -n "$1" logs -l "job-name=$name" --tail "${3:-50}" --all-containers=true --ignore-errors=true 2>&1 || true
}

migration_job_report() {
  local row rc=0 name
  row="$(migration_job_status "$1" "$2")" || rc=$?
  if (( rc != 0 )); then echo "migration job: $row"; return 0; fi
  if [[ -z "$row" ]]; then echo "migration job: none found for release $2 in $1 (removed after its TTL?)"; return 0; fi
  name="${row%%|*}"
  echo "migration job: $row  (name|succeeded|failed|FailedCondition)"
  kubectl -n "$1" get job "$name" -o wide 2>&1 || true
  echo "migration job logs (last 50 lines per Pod):"
  migration_job_logs "$1" "$2" 50
}

# For the report after a FAILED deploy only. `--rollback-on-failure` has already acted, so the release
# is gone (failed first install) or back on the previous revision, and its manifest no longer names the
# Job that failed. The failed Job is still there (helm.sh/resource-policy keep), so take the NEWEST
# migration Job of this release by creationTimestamp. Always returns 0.
migration_job_report_newest() {
  local ns="$1" release="$2" out rc=0 row name
  out="$(kubectl -n "$ns" get job -l "app.kubernetes.io/component=migration,app.kubernetes.io/instance=$release" \
    --sort-by=.metadata.creationTimestamp \
    -o jsonpath='{range .items[*]}{.metadata.name}{"|"}{.status.succeeded}{"|"}{.status.failed}{"|"}{.status.conditions[?(@.type=="Failed")].status}{"\n"}{end}' 2>&1)" || rc=$?
  if (( rc != 0 )); then echo "migration job: kubectl get job failed: $(tail -n 1 <<<"$out")"; return 0; fi
  row="$(tail -n 1 <<<"$out")"
  if [[ -z "$row" ]]; then echo "migration job: none found for release $release in $ns"; return 0; fi
  name="${row%%|*}"
  echo "migration job: $row  (name|succeeded|failed|FailedCondition)"
  kubectl -n "$ns" get job "$name" -o wide 2>&1 || true
  echo "migration job logs (last 50 lines per Pod):"
  kubectl -n "$ns" logs -l "job-name=$name" --tail 50 --all-containers=true --ignore-errors=true 2>&1 || true
}

# A failed FIRST install is uninstalled, so the retry is revision 1 again and renders the same Job name;
# Helm would adopt the kept, Failed Job and the retry would fail at once. Delete this release's Failed Jobs.
migration_job_purge_failed() {
  local ns="$1" release="$2" out rc=0 name cond
  out="$(kubectl -n "$ns" get job -l "app.kubernetes.io/component=migration,app.kubernetes.io/instance=$release" \
    -o jsonpath='{range .items[*]}{.metadata.name}{"|"}{.status.conditions[?(@.type=="Failed")].status}{"\n"}{end}' 2>&1)" || rc=$?
  if (( rc != 0 )); then echo "could not list migration Jobs of $release: $(tail -n 1 <<<"$out")"; return 4; fi
  while IFS='|' read -r name cond; do
    [[ "$cond" == "True" && -n "$name" ]] || continue
    echo "deleting the Failed migration Job $name of $release (first install; a retry would adopt it)"
    kubectl delete job "$name" -n "$ns" 2>&1 || true
  done <<<"$out"
}
