#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# deploy-verify.bats — scripts/ci/deploy-verify.sh against a stub kubectl.
#
# The stub answers from environment variables, so no cluster is ever contacted.
# Each of the 5 checks has a passing and a failing case, plus a digest mismatch
# and missing inputs.
# -----------------------------------------------------------------------------

D_BACKEND="sha256:$(printf 'a%.0s' {1..64})"
D_FRONTEND="sha256:$(printf 'b%.0s' {1..64})"
D_OTHER="sha256:$(printf 'c%.0s' {1..64})"

setup() {
  SCRIPT="$(dirname "$BATS_TEST_DIRNAME")/../scripts/ci/deploy-verify.sh"
  mkdir -p "$BATS_TEST_TMPDIR/bin"
  cat > "$BATS_TEST_TMPDIR/bin/kubectl" <<'STUB'
#!/usr/bin/env bash
# Stub kubectl: dispatch on the arguments, answer from STUB_* variables.
args="$*"
echo "$args" >> "${STUB_LOG:-/dev/null}"
comp() { sed -n 's/.*app.kubernetes.io\/component=\([a-z-]*\).*/\1/p' <<<"$args"; }
case "$args" in
  *"get secret"*) printf '%s\n' "${STUB_RELEASE-secret/sh.helm.release.v1.rel.v3}" ;;
  *"get job"*)
    [[ -z "${STUB_KUBECTL_FAIL-}" ]] || { echo "connection refused" >&2; exit 1; }
    if [[ -n "${STUB_JOBS-}" ]]; then printf 'job-r1|%s\n' "$STUB_JOBS"
    else echo 'Error from server (NotFound): jobs.batch "job-r1" not found' >&2; exit 1; fi ;;
  *"get pods"*containerStatuses*)
    c="$(comp)"; c="${c//-/_}"; v="STUB_IMAGES_${c^^}"
    printf '%s\n' "${!v-}" ;;
  *"get pods"*app.kubernetes.io/name=*) printf '%s\n' "${STUB_PODS-}" ;;
  *"get pods"*) c="$(comp)"; n="${c//-/_}"; v="STUB_PODNAMES_${n^^}"; printf '%s\n' "${!v-pod-$c|}" ;;
  *exec*)
    case " ${STUB_EXEC_FAIL-} " in *" $(sed -n 's/.* exec \([^ ]*\) --.*/\1/p' <<<"$args") "*) exit 1 ;; esac
    echo ok ;;
  *) echo "stub kubectl: unexpected: $args" >&2; exit 99 ;;
esac
STUB
  cat > "$BATS_TEST_TMPDIR/bin/helm" <<'STUB'
#!/usr/bin/env bash
[[ -z "${STUB_NO_RELEASE-}" ]] || { echo "Error: release: not found" >&2; exit 1; }
printf -- '---\nkind: Job\nmetadata:\n  name: job-r1\n  labels:\n    app.kubernetes.io/component: migration\n'
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/kubectl" "$BATS_TEST_TMPDIR/bin/helm"
  export PATH="$BATS_TEST_TMPDIR/bin:$PATH"
  export STUB_LOG="$BATS_TEST_TMPDIR/kubectl.log"
  export STUB_JOBS="1||"
  export STUB_PODS=$'p1|Running|\np2|Running|'
  export STUB_IMAGES_BACKEND="p1||registry/img@$D_BACKEND "
  export STUB_IMAGES_FRONTEND="p1||registry/img@$D_FRONTEND "
  export RELEASE=rel NAMESPACE=rel-dev SERVICE_NAME=svc
  export BACKEND_IMAGE_DIGEST="$D_BACKEND" FRONTEND_IMAGE_DIGEST="$D_FRONTEND"
}

@test "all five checks pass" {
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  for n in 1 2 3 4 5; do [[ "$output" == *"== Check $n:"* ]]; done
  [[ "$output" == *"all checks passed"* ]]
}

@test "flags override the environment" {
  run bash "$SCRIPT" --release other --namespace other-ns --name other
  [ "$status" -eq 0 ]
  grep -q -- "-n other-ns" "$STUB_LOG"
  grep -q "owner=helm,name=other,status=deployed" "$STUB_LOG"
}

@test "check 1 fails when no deployed release secret exists" {
  STUB_RELEASE="" run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"ERROR: release rel is not deployed"* ]]
}

@test "check 2 passes when the migration job was already cleaned up" {
  STUB_JOBS="" run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"no migration job"* ]]
}

@test "check 2 fails when the migration job did not succeed" {
  STUB_JOBS="||" run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"ERROR: migration job did not complete"* ]]
}

@test "check 2 fails on a migration job that failed (failed count, no succeeded)" {
  STUB_JOBS="|1|" run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"ERROR: migration job failed"* ]]
}

@test "check 2 reads the job named in the release manifest" {
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  grep -q "get job job-r1" "$STUB_LOG"
}

@test "check 2 fails clearly when kubectl itself fails, instead of aborting" {
  STUB_KUBECTL_FAIL=1 run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"ERROR: migration job lookup failed"* ]]
}

@test "check 2 fails when the release manifest cannot be read" {
  STUB_NO_RELEASE=1 run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"ERROR: migration job lookup failed"* ]]
}

@test "check 2 fails on a job with the Failed condition" {
  STUB_JOBS="|4|True" run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"ERROR: migration job failed"* ]]
}

@test "check 2 accepts a job that succeeded after a failed attempt" {
  STUB_JOBS="1|1|" run bash "$SCRIPT"
  [ "$status" -eq 0 ]
}

@test "check 3 fails when a pod is not running" {
  STUB_PODS=$'p1|Running|\np2|CrashLoopBackOff|' run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"ERROR: not all pods are Running"* ]]
}

@test "check 3 fails when there are no pods" {
  STUB_PODS="" run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"ERROR: not all pods are Running"* ]]
}

@test "check 3 accepts a completed pod" {
  STUB_PODS=$'p1|Running|\nmig|Succeeded|' run bash "$SCRIPT"
  [ "$status" -eq 0 ]
}

@test "check 4 fails on a digest mismatch" {
  STUB_IMAGES_FRONTEND="p1||registry/img@sha256:$(printf 'd%.0s' {1..64}) " run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"ERROR: frontend pod p1 digest mismatch"* ]]
}

@test "check 4 fails when one of several pods runs the old digest" {
  STUB_IMAGES_BACKEND=$'p1||registry/img@'"$D_BACKEND"$' \np2||registry/img@sha256:'"$(printf 'e%.0s' {1..64})" run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"backend pod p2 digest mismatch"* ]]
}

@test "check 4 fails when a component has no pods" {
  STUB_IMAGES_FRONTEND="" run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"ERROR: no frontend pods found"* ]]
}

@test "check 5 fails when the backend health route fails" {
  STUB_EXEC_FAIL="pod-backend" run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"ERROR: backend health failed"* ]]
}

@test "check 5 fails when the frontend health route fails" {
  STUB_EXEC_FAIL="pod-frontend" run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"ERROR: frontend health failed"* ]]
}

@test "check 5 probes the backend with Python and the frontend with Node (neither image has curl)" {
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  grep -q "exec pod-backend -- /app/.venv/bin/python -c .*/api/v1/health" "$STUB_LOG"
  grep -q "exec pod-frontend -- /nodejs/bin/node -e .*/" "$STUB_LOG"
  run grep -q " -- curl" "$STUB_LOG"
  [ "$status" -ne 0 ]
}

@test "a missing release is a usage error" {
  unset RELEASE
  run bash "$SCRIPT"
  [ "$status" -eq 2 ]
  [[ "$output" == *"RELEASE"* ]]
}

@test "a missing namespace is a usage error" {
  unset NAMESPACE
  run bash "$SCRIPT"
  [ "$status" -eq 2 ]
}

@test "a missing expected digest is a usage error" {
  unset FRONTEND_IMAGE_DIGEST
  run bash "$SCRIPT"
  [ "$status" -eq 2 ]
  [[ "$output" == *"FRONTEND_IMAGE_DIGEST"* ]]
}

@test "a malformed expected digest is a usage error" {
  BACKEND_IMAGE_DIGEST="latest" run bash "$SCRIPT"
  [ "$status" -eq 2 ]
  [[ "$output" == *"BACKEND_IMAGE_DIGEST"* ]]
}

@test "an unknown flag is a usage error" {
  run bash "$SCRIPT" --bogus
  [ "$status" -eq 2 ]
}

@test "kubectl is never asked for anything but reads and exec" {
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  run grep -E " (apply|delete|patch|create|replace|edit|scale|rollout) " "$STUB_LOG"
  [ "$status" -eq 1 ]
}

@test "terminating pods are ignored: an old draining pod passes check 4 and check 5 skips it" {
  STUB_PODS=$'new|Running|\nold|Running|2026-01-01T00:00:00Z'
  STUB_IMAGES_BACKEND=$'new||registry/img@'"$D_BACKEND"$' \nold|2026-01-01T00:00:00Z|registry/img@sha256:'"$(printf 'e%.0s' {1..64})"' '
  STUB_PODNAMES_BACKEND=$'old|2026-01-01T00:00:00Z\nnew|'
  STUB_EXEC_FAIL="old"
  export STUB_PODS STUB_IMAGES_BACKEND STUB_PODNAMES_BACKEND STUB_EXEC_FAIL
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  grep -q "exec new -- /app/.venv/bin/python" "$STUB_LOG"
  run grep -q "exec old" "$STUB_LOG"
  [ "$status" -ne 0 ]
}

@test "only terminating pods left is a failure" {
  STUB_PODS="old|Running|2026-01-01T00:00:00Z"
  run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"none found"* ]]
}

@test "a hyphenated component uses a sanitised digest variable" {
  export COMPONENTS="backend web-ui" WEB_UI_IMAGE_DIGEST="$D_OTHER"
  export STUB_IMAGES_WEB_UI="p1||registry/img@$D_OTHER "
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
}

@test "check 4 passes a multi-container pod where only the app container has the expected digest" {
  STUB_IMAGES_BACKEND="p1||registry/sidecar@sha256:$(printf 'f%.0s' {1..64}) registry/img@$D_BACKEND " run bash "$SCRIPT"
  [ "$status" -eq 0 ]
}

@test "check 4 fails a multi-container pod where no container has the expected digest" {
  STUB_IMAGES_BACKEND="p1||registry/sidecar@sha256:$(printf 'f%.0s' {1..64}) registry/img@sha256:$(printf 'e%.0s' {1..64}) " run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"backend pod p1 digest mismatch"* ]]
}

@test "check 4 accepts a docker-pullable imageID" {
  STUB_IMAGES_BACKEND="p1||docker-pullable://registry/img@$D_BACKEND " run bash "$SCRIPT"
  [ "$status" -eq 0 ]
}

@test "check 4 fails a docker-pullable imageID with another digest" {
  STUB_IMAGES_BACKEND="p1||docker-pullable://registry/img@sha256:$(printf 'e%.0s' {1..64}) " run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"backend pod p1 digest mismatch"* ]]
}
