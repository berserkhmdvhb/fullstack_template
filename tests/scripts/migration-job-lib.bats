#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# migration-job-lib.bats — scripts/ci/migration-job-lib.sh against stub helm and kubectl.
# The Job is the one NAMED in the release's current manifest (helm get manifest), then its status is
# read. STUB_MANIFEST_JOB names it; STUB_JOBS holds the cluster's Jobs, "name|succeeded|failed|failedCond"
# per line; STUB_LEFTOVER holds "instance|name|failedCond" rows for the first-install purge.
# -----------------------------------------------------------------------------

setup() {
  LIB="$(dirname "$BATS_TEST_DIRNAME")/../scripts/ci/migration-job-lib.sh"
  mkdir -p "$BATS_TEST_TMPDIR/bin"
  export STUB_LOG="$BATS_TEST_TMPDIR/calls.log"
  : > "$STUB_LOG"
  cat > "$BATS_TEST_TMPDIR/bin/helm" <<'STUB'
#!/usr/bin/env bash
echo "helm $*" >> "$STUB_LOG"
[[ "$1 $2" == "get manifest" ]] || { echo "stub helm: unexpected: $*" >&2; exit 99; }
[[ -z "${STUB_NO_RELEASE-}" ]] || { echo "Error: release: not found" >&2; exit 1; }
[[ -z "${STUB_HELM_FAIL-}" ]] || { echo "Error: Kubernetes cluster unreachable" >&2; exit 1; }
cat <<MANIFEST
---
# Source: your-service-name/templates/backend-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: rel-backend
  labels:
    app.kubernetes.io/component: backend
spec:
  template:
    metadata:
      labels:
        app.kubernetes.io/component: migration
---
# Source: your-service-name/templates/migration-job.yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: ${STUB_MANIFEST_JOB-rel-migrate-r1}
  labels:
    app.kubernetes.io/component: migration
MANIFEST
STUB
  cat > "$BATS_TEST_TMPDIR/bin/kubectl" <<'STUB'
#!/usr/bin/env bash
args="$*"
echo "kubectl $args" >> "$STUB_LOG"
[[ -z "${STUB_KUBECTL_FAIL-}" ]] || { echo "Unable to connect to the server: connection refused" >&2; exit 1; }
case "$args" in
  *"get job -l"*)
    inst="$(sed -n 's/.*app.kubernetes.io\/instance=\([^, ]*\).*/\1/p' <<<"$args")"
    awk -F'|' -v i="$inst" '$1==i {print $2"|"$3}' <<<"${STUB_LEFTOVER-}" ;;
  *"get job "*"jsonpath"*)
    name="$(sed -n 's/.* get job \([^ ]*\) .*/\1/p' <<<"$args")"
    row="$(awk -F'|' -v n="$name" '$1==n' <<<"${STUB_JOBS-}")"
    if [[ -n "$row" ]]; then echo "$row"
    else echo "Error from server (NotFound): jobs.batch \"$name\" not found" >&2; exit 1; fi ;;
  *"get job "*) echo "job summary" ;;
  *logs*) echo "log line from the migrate container" ;;
  *"delete job"*) echo "deleted" ;;
  *) echo "stub kubectl: unexpected: $args" >&2; exit 99 ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/"*
  export PATH="$BATS_TEST_TMPDIR/bin:$PATH"
  export MIGRATION_POLL_SLEEP=0
  # shellcheck source=/dev/null
  source "$LIB"
}

@test "the job is the one named in the release's current manifest" {
  run migration_job_name ns rel
  [ "$status" -eq 0 ]
  [ "$output" = "rel-migrate-r1" ]
  grep -q "helm get manifest rel -n ns" "$STUB_LOG"
}

@test "after a rollback the manifest's older Complete job is chosen, not the newer Failed one" {
  STUB_MANIFEST_JOB=rel-migrate-r2 \
  STUB_JOBS=$'rel-migrate-r2|1||\nrel-migrate-r3||4|True' run migration_job_status ns rel
  [ "$status" -eq 0 ]
  [ "$output" = "rel-migrate-r2|1||" ]
}

@test "with no release the result is a clear no-release message and status 3" {
  STUB_NO_RELEASE=1 run migration_job_status ns rel
  [ "$status" -eq 3 ]
  [[ "$output" == *"no release rel in ns"* ]]
}

@test "a helm failure other than not-found is status 4 with a message" {
  STUB_HELM_FAIL=1 run migration_job_status ns rel
  [ "$status" -eq 4 ]
  [[ "$output" == *"helm get manifest failed"* ]]
}

@test "a kubectl failure returns 4 with a message instead of aborting a strict caller" {
  # shellcheck disable=SC2016
  run bash -c 'set -Eeuo pipefail; trap "echo ERRTRAP" ERR; source "$1"; rc=0; row="$(migration_job_status ns rel)" || rc=$?; echo "rc=$rc row=$row"' _ "$LIB"
  [ "$status" -eq 0 ]
  [[ "$output" != *ERRTRAP* ]]
  # shellcheck disable=SC2016
  STUB_KUBECTL_FAIL=1 run bash -c 'set -Eeuo pipefail; source "$1"; rc=0; row="$(migration_job_status ns rel)" || rc=$?; echo "rc=$rc row=$row"' _ "$LIB"
  [ "$status" -eq 0 ]
  [[ "$output" == *"rc=4"* ]]
  [[ "$output" == *"kubectl"* ]]
}

@test "a job already removed after its TTL is an empty row, not an error" {
  STUB_JOBS="" run migration_job_status ns rel
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "a manifest with no migration job is status 5" {
  STUB_MANIFEST_JOB="" run migration_job_name ns rel
  [ "$status" -eq 5 ]
}

@test "wait returns 0 for a Complete job" {
  STUB_JOBS=$'rel-migrate-r1|1||' run migration_job_wait ns rel 5
  [ "$status" -eq 0 ]
}

@test "wait returns 1 for a Failed job" {
  STUB_JOBS=$'rel-migrate-r1||4|True' run migration_job_wait ns rel 5
  [ "$status" -eq 1 ]
  [[ "$output" == *"failed"* ]]
}

@test "wait times out (2) on a job that is still running" {
  STUB_JOBS=$'rel-migrate-r1|||' run migration_job_wait ns rel 0
  [ "$status" -eq 2 ]
  [[ "$output" == *"did not finish"* ]]
}

@test "wait returns 2 when the job object does not exist" {
  STUB_JOBS="" run migration_job_wait ns rel 0
  [ "$status" -eq 2 ]
  [[ "$output" == *"no migration job"* ]]
}

@test "wait passes the lookup failure on (3 no release, 4 tool failure)" {
  STUB_NO_RELEASE=1 run migration_job_wait ns rel 0
  [ "$status" -eq 3 ]
  [[ "$output" == *"no release"* ]]
  STUB_KUBECTL_FAIL=1 run migration_job_wait ns rel 0
  [ "$status" -eq 4 ]
}

@test "the report prints the job status and a bounded tail of its logs" {
  STUB_JOBS=$'rel-migrate-r1||4|True' run migration_job_report ns rel
  [ "$status" -eq 0 ]
  [[ "$output" == *"rel-migrate-r1"* ]]
  [[ "$output" == *"log line from the migrate container"* ]]
  grep -q -- "logs -l job-name=rel-migrate-r1 --tail 50" "$STUB_LOG"
}

@test "the report says so when there is no release, and still returns 0" {
  STUB_NO_RELEASE=1 run migration_job_report ns rel
  [ "$status" -eq 0 ]
  [[ "$output" == *"no release rel in ns"* ]]
}

@test "the purge deletes only Failed jobs of this release and logs their names" {
  STUB_LEFTOVER=$'rel|rel-migrate-r1|True\nrel|rel-migrate-r2|\nother|other-migrate-r1|True' \
    run migration_job_purge_failed ns rel
  [ "$status" -eq 0 ]
  [[ "$output" == *"rel-migrate-r1"* ]]
  grep -q "get job -l app.kubernetes.io/component=migration,app.kubernetes.io/instance=rel" "$STUB_LOG"
  grep -q "delete job rel-migrate-r1 -n ns" "$STUB_LOG"
  run grep -c "delete job" "$STUB_LOG"
  [ "$output" = "1" ]
}

@test "the purge deletes nothing when there is no failed job" {
  STUB_LEFTOVER=$'rel|rel-migrate-r1|' run migration_job_purge_failed ns rel
  [ "$status" -eq 0 ]
  run grep -c "delete job" "$STUB_LOG"
  [ "$output" = "0" ]
}
