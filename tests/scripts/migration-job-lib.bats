#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# migration-job-lib.bats — scripts/ci/migration-job-lib.sh against a stub kubectl.
# The stub answers from STUB_JOBS: lines "instance|name|succeeded|failed|failedCondition", oldest
# first, filtered by the instance label of the selector, as the API server would.
# -----------------------------------------------------------------------------

setup() {
  LIB="$(dirname "$BATS_TEST_DIRNAME")/../scripts/ci/migration-job-lib.sh"
  mkdir -p "$BATS_TEST_TMPDIR/bin"
  export STUB_LOG="$BATS_TEST_TMPDIR/kubectl.log"
  : > "$STUB_LOG"
  cat > "$BATS_TEST_TMPDIR/bin/kubectl" <<'STUB'
#!/usr/bin/env bash
args="$*"
echo "$args" >> "$STUB_LOG"
inst="$(sed -n 's/.*app.kubernetes.io\/instance=\([^, ]*\).*/\1/p' <<<"$args")"
case "$args" in
  *"get job"*"metadata.name"*"succeeded"*)
    awk -F'|' -v i="$inst" '$1==i {print $2"|"$3"|"$4"|"$5}' <<<"${STUB_JOBS-}" ;;
  *"get job"*"metadata.name"*)
    awk -F'|' -v i="$inst" '$1==i {print $2}' <<<"${STUB_JOBS-}" ;;
  *"get job"*) echo "job summary" ;;
  *logs*) echo "log line from the migrate container" ;;
  *) echo "stub kubectl: unexpected: $args" >&2; exit 99 ;;
esac
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/kubectl"
  export PATH="$BATS_TEST_TMPDIR/bin:$PATH"
  export MIGRATION_POLL_SLEEP=0
  # shellcheck source=/dev/null
  source "$LIB"
}

@test "the newest of two jobs is chosen" {
  STUB_JOBS=$'rel|rel-migrate-r1|1||\nrel|rel-migrate-r2|||' run migration_job_name ns rel
  [ "$status" -eq 0 ]
  [ "$output" = "rel-migrate-r2" ]
}

@test "another release's job is never picked" {
  STUB_JOBS=$'rel|rel-migrate-r1|1||\nother|other-migrate-r9|||' run migration_job_name ns rel
  [ "$output" = "rel-migrate-r1" ]
  STUB_JOBS=$'other|other-migrate-r9|||' run migration_job_name ns rel
  [ -z "$output" ]
}

@test "the lookup selects by the migration component and the release instance, sorted by creation" {
  STUB_JOBS=$'rel|j|1||' migration_job_status ns rel >/dev/null
  grep -q "app.kubernetes.io/component=migration,app.kubernetes.io/instance=rel" "$STUB_LOG"
  grep -q -- "--sort-by=.metadata.creationTimestamp" "$STUB_LOG"
  grep -q -- "-n ns" "$STUB_LOG"
}

@test "the status row is that of the newest job" {
  STUB_JOBS=$'rel|j1|1||\nrel|j2||2|True' run migration_job_status ns rel
  [ "$output" = "j2||2|True" ]
}

@test "wait returns 0 for a Complete job" {
  STUB_JOBS=$'rel|j1|1||' run migration_job_wait ns rel 5
  [ "$status" -eq 0 ]
}

@test "wait returns 1 for a Failed job" {
  STUB_JOBS=$'rel|j1||4|True' run migration_job_wait ns rel 5
  [ "$status" -eq 1 ]
  [[ "$output" == *"failed"* ]]
}

@test "wait times out (2) on a job that is still running" {
  STUB_JOBS=$'rel|j1|||' run migration_job_wait ns rel 0
  [ "$status" -eq 2 ]
  [[ "$output" == *"did not finish"* ]]
}

@test "wait returns 2 when there is no job for the release" {
  STUB_JOBS=$'other|j1|1||' run migration_job_wait ns rel 0
  [ "$status" -eq 2 ]
  [[ "$output" == *"no migration job"* ]]
}

@test "the report prints the job status and a bounded tail of its logs" {
  STUB_JOBS=$'rel|rel-migrate-r2||4|True' run migration_job_report ns rel
  [ "$status" -eq 0 ]
  [[ "$output" == *"rel-migrate-r2"* ]]
  [[ "$output" == *"log line from the migrate container"* ]]
  grep -q -- "logs -l job-name=rel-migrate-r2 --tail 50" "$STUB_LOG"
}
