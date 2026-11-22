#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# image-checks.bats — scripts/ci/image-checks.sh against a stub docker.
#
# The stub answers from STUB_* variables, so no image is built or run. Each check has a
# passing and a failing case; the container is removed on failure; the wait bound is
# tested as arithmetic and as a run that exceeds it.
# -----------------------------------------------------------------------------

bats_require_minimum_version 1.5.0

NS=1000000000

setup() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
  SCRIPT="$ROOT/scripts/ci/image-checks.sh"
  export IMAGE_CHECKS_POLL=0.05
  export STUB_LOG="$BATS_TEST_TMPDIR/docker.log"
  : > "$STUB_LOG"
  export IMAGE_CHECKS_MANIFEST="$BATS_TEST_TMPDIR/images.yml"
  cat > "$IMAGE_CHECKS_MANIFEST" <<'YML'
schema_version: 1
images:
  - id: slim
    repository: "ghcr.io/o/r/slim"
    dockerfile: Dockerfile.slim
    distroless: true
    run_env:
      MODE: alone
    run_args: ["--read-only"]
  - id: full
    repository: "ghcr.io/o/r/full"
    dockerfile: Dockerfile.full
    distroless: false
  - id: probed
    repository: "ghcr.io/o/r/probed"
    dockerfile: Dockerfile.probed
    distroless: true
    healthcheck_exempt: "no probe binary; Kubernetes probes the health listener"
YML
  # A healthy, non-root image with every label, as the stub default.
  export STUB_USER=10001
  export STUB_HC_TEST='[CMD /probe]'
  export STUB_HC_TIMES="0 $((1 * NS)) $((1 * NS)) 1"
  export STUB_STATES="$BATS_TEST_TMPDIR/states"
  echo "running healthy" > "$STUB_STATES"
  mkdir -p "$BATS_TEST_TMPDIR/rootfs/usr/bin" "$BATS_TEST_TMPDIR/rootfs/etc"
  touch "$BATS_TEST_TMPDIR/rootfs/usr/bin/python3" "$BATS_TEST_TMPDIR/rootfs/etc/passwd"
  export STUB_TAR="$BATS_TEST_TMPDIR/export.tar"
  tar -C "$BATS_TEST_TMPDIR/rootfs" -cf "$STUB_TAR" .
  mkdir -p "$BATS_TEST_TMPDIR/bin"
  cp "$BATS_TEST_DIRNAME/fixtures/docker-stub.sh" "$BATS_TEST_TMPDIR/bin/docker"
  PATH="$BATS_TEST_TMPDIR/bin:$PATH"
}

placeholder_source() {
  python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); import adopt_stamp; print(adopt_stamp.OLD_SOURCE_URL)' "$ROOT/infra/adapt"
}

@test "all checks pass for a healthy distroless image" {
  run bash "$SCRIPT" slim ghcr.io/o/r/slim:t
  [ "$status" -eq 0 ]
  [[ "$output" == *"user: ok"* ]]
  [[ "$output" == *"no shell: ok"* ]]
  [[ "$output" == *"healthcheck: ok"* ]]
  [[ "$output" == *"labels: ok"* ]]
}

@test "usage: missing arguments fail" {
  run bash "$SCRIPT" slim
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage:"* ]]
}

@test "an unknown image id fails" {
  run bash "$SCRIPT" nope ghcr.io/o/r/x:t
  [ "$status" -ne 0 ]
  [[ "$output" == *"::error::"*"nope"* ]]
}

@test "user: root (uid 0) fails" {
  STUB_USER=0 run bash "$SCRIPT" slim ref:t
  [ "$status" -eq 1 ]
  [[ "$output" == *"::error::"*"user"* ]]
}

@test "user: an empty user (root by default) fails" {
  STUB_USER='' run bash "$SCRIPT" slim ref:t
  [ "$status" -eq 1 ]
}

@test "user: a name instead of a number fails" {
  STUB_USER=appuser run bash "$SCRIPT" slim ref:t
  [ "$status" -eq 1 ]
  [[ "$output" == *"::error::"*"appuser"* ]]
}

@test "user: uid:gid with a non-zero uid passes" {
  STUB_USER=10001:10001 run bash "$SCRIPT" slim ref:t
  [ "$status" -eq 0 ]
}

@test "distroless: a shell in the filesystem fails" {
  touch "$BATS_TEST_TMPDIR/rootfs/usr/bin/sh"
  tar -C "$BATS_TEST_TMPDIR/rootfs" -cf "$STUB_TAR" .
  run bash "$SCRIPT" slim ref:t
  [ "$status" -eq 1 ]
  [[ "$output" == *"::error::"*"/usr/bin/sh"* ]]
}

@test "distroless: a fetcher in /usr/local/bin fails" {
  mkdir -p "$BATS_TEST_TMPDIR/rootfs/usr/local/bin"
  touch "$BATS_TEST_TMPDIR/rootfs/usr/local/bin/curl"
  tar -C "$BATS_TEST_TMPDIR/rootfs" -cf "$STUB_TAR" .
  run bash "$SCRIPT" slim ref:t
  [ "$status" -eq 1 ]
  [[ "$output" == *"/usr/local/bin/curl"* ]]
}

@test "distroless: a similar name (shfmt, python3-pip) is not a shell" {
  touch "$BATS_TEST_TMPDIR/rootfs/usr/bin/shfmt" "$BATS_TEST_TMPDIR/rootfs/usr/bin/pipe"
  tar -C "$BATS_TEST_TMPDIR/rootfs" -cf "$STUB_TAR" .
  run bash "$SCRIPT" slim ref:t
  [ "$status" -eq 0 ]
}

@test "non-distroless: a shell is allowed and the log says the check is skipped" {
  touch "$BATS_TEST_TMPDIR/rootfs/usr/bin/sh"
  tar -C "$BATS_TEST_TMPDIR/rootfs" -cf "$STUB_TAR" .
  run bash "$SCRIPT" full ref:t
  [ "$status" -eq 0 ]
  [[ "$output" == *"no shell: skipped"* ]]
  run ! grep -q '^export' "$STUB_LOG"
}

@test "the filesystem is only inspected, never executed" {
  run bash "$SCRIPT" slim ref:t
  [ "$status" -eq 0 ]
  # the only docker run is the health container, never an exec or a run of the image's tools
  run ! grep -q '^exec' "$STUB_LOG"
  [ "$(grep -c '^run ' "$STUB_LOG")" -eq 1 ]
}

@test "labels: a missing label fails and names it" {
  STUB_LABEL_REVISION='' run bash "$SCRIPT" slim ref:t
  [ "$status" -eq 1 ]
  [[ "$output" == *"::error::"*"revision"* ]]
}

@test "labels: unknown passes without --strict" {
  STUB_LABEL_VERSION=unknown run bash "$SCRIPT" slim ref:t
  [ "$status" -eq 0 ]
}

@test "labels: unknown fails with --strict" {
  STUB_LABEL_CREATED=unknown run bash "$SCRIPT" --strict slim ref:t
  [ "$status" -eq 1 ]
  [[ "$output" == *"::error::"*"created"*"unknown"* ]]
}

@test "labels: the template placeholder source passes without --strict and fails with it" {
  STUB_LABEL_SOURCE="$(placeholder_source)" run bash "$SCRIPT" slim ref:t
  [ "$status" -eq 0 ]
  STUB_LABEL_SOURCE="$(placeholder_source)" run bash "$SCRIPT" --strict slim ref:t
  [ "$status" -eq 1 ]
  [[ "$output" == *"::error::"*"placeholder"* ]]
}

@test "labels: real values pass with --strict" {
  STUB_LABEL_SOURCE="https://github.com/acme/widget" run bash "$SCRIPT" --strict slim ref:t
  [ "$status" -eq 0 ]
}

@test "healthcheck: an image without one fails" {
  STUB_HC_TEST='' run bash "$SCRIPT" slim ref:t
  [ "$status" -eq 1 ]
  [[ "$output" == *"::error::"*"HEALTHCHECK"* ]]
}

@test "healthcheck: an exempt image is not run, and the log gives the manifest's reason" {
  STUB_HC_TEST="" run bash "$SCRIPT" probed ref:t
  [ "$status" -eq 0 ]
  [[ "$output" == *"healthcheck: skipped (no probe binary; Kubernetes probes the health listener)"* ]]
  run grep -q '^run ' "$STUB_LOG"
  [ "$status" -ne 0 ]
}

@test "healthcheck: the exemption is per image; the others still need one" {
  STUB_HC_TEST='' run bash "$SCRIPT" slim ref:t
  [ "$status" -eq 1 ]
}

@test "healthcheck: NONE counts as no healthcheck" {
  STUB_HC_TEST='[NONE]' run bash "$SCRIPT" slim ref:t
  [ "$status" -eq 1 ]
}

@test "healthcheck: starting then healthy passes" {
  printf 'running starting\nrunning starting\nrunning healthy\n' > "$STUB_STATES"
  run bash "$SCRIPT" slim ref:t
  [ "$status" -eq 0 ]
}

@test "healthcheck: the container runs without published ports and with the manifest's settings" {
  run bash "$SCRIPT" slim ref:t
  [ "$status" -eq 0 ]
  line="$(grep '^run ' "$STUB_LOG")"
  [[ "$line" == *"-e MODE=alone"* ]]
  [[ "$line" == *"--read-only"* ]]
  [[ "$line" != *" -p "* && "$line" != *"-P"* && "$line" != *"--publish"* ]]
  [[ "$line" =~ --name\ image-checks-slim- ]]
}

@test "healthcheck: unhealthy fails and shows the health log and container logs" {
  echo "running unhealthy" > "$STUB_STATES"
  run bash "$SCRIPT" slim ref:t
  [ "$status" -eq 1 ]
  [[ "$output" == *"::error::"*"unhealthy"* ]]
  [[ "$output" == *"probe refused"* ]]
  [[ "$output" == *"app log line"* ]]
}

@test "healthcheck: an exited container fails" {
  echo "exited starting" > "$STUB_STATES"
  run bash "$SCRIPT" slim ref:t
  [ "$status" -eq 1 ]
  [[ "$output" == *"::error::"*"exited"* ]]
  [[ "$output" == *"app log line"* ]]
}

@test "healthcheck: an exited container is reported as exited even when docker marks it unhealthy" {
  echo "exited unhealthy" > "$STUB_STATES"
  run bash "$SCRIPT" slim ref:t
  [ "$status" -eq 1 ]
  [[ "$output" == *"::error::"*"exited"* ]]
}

@test "healthcheck: staying in starting past the bound fails" {
  echo "running starting" > "$STUB_STATES"
  SECONDS=0
  run bash "$SCRIPT" slim ref:t
  [ "$status" -eq 1 ]
  [[ "$output" == *"::error::"*"not healthy within 2s"* ]]
  [ "$SECONDS" -le 10 ]
}

@test "the container is removed by its exact name on failure and on success" {
  echo "running unhealthy" > "$STUB_STATES"
  run bash "$SCRIPT" slim ref:t
  name="$(sed -n 's/^run .*--name \([^ ]*\) .*/\1/p' "$STUB_LOG")"
  [ -n "$name" ]
  grep -qx "rm -f $name" "$STUB_LOG"
  : > "$STUB_LOG"; echo "running healthy" > "$STUB_STATES"
  run bash "$SCRIPT" slim ref:t
  name="$(sed -n 's/^run .*--name \([^ ]*\) .*/\1/p' "$STUB_LOG")"
  grep -qx "rm -f $name" "$STUB_LOG"
}

@test "the filesystem container is removed too" {
  run bash "$SCRIPT" slim ref:t
  name="$(sed -n 's/^create --name \([^ ]*\) .*/\1/p' "$STUB_LOG")"
  [ -n "$name" ]
  grep -qx "rm -f $name" "$STUB_LOG"
}

@test "nothing but exact-name removal is used to clean up" {
  run bash "$SCRIPT" slim ref:t
  run ! grep -Eq '^(ps|stop|kill|prune|system)' "$STUB_LOG"
  run ! grep -q 'prune' "$STUB_LOG"
}

# The bound: start period + interval x retries + timeout, in whole seconds, with Docker's defaults
# (interval 30s, timeout 30s, retries 3) for zero values.
bound() { bash -c 'source "$1"; wait_bound "${@:2}"' _ "$SCRIPT" "$@"; }

@test "wait bound: start + interval x retries + timeout" {
  run bound $((30 * NS)) $((10 * NS)) $((5 * NS)) 5
  [ "$output" -eq 85 ]
}

@test "wait bound: the backend's settings" {
  run bound $((15 * NS)) $((10 * NS)) $((5 * NS)) 3
  [ "$output" -eq 50 ]
}

@test "wait bound: zero values take Docker's defaults" {
  run bound 0 0 0 0
  [ "$output" -eq 120 ]
}

@test "wait bound: fractions round up" {
  run bound 0 $((1500000000)) $((500000000)) 1
  [ "$output" -eq 2 ]
}

# A docker call that fails is reported with ::error:: naming the step and the image, and fails closed.
@test "a failing docker call is reported with ::error:: naming the step and the image" {
  for step in user hctest labels create export run state; do
    STUB_FAIL="$step" run bash "$SCRIPT" slim ghcr.io/o/r/slim:t
    [ "$status" -eq 1 ] || { echo "step $step: status $status"; return 1; }
    [[ "$output" == *"::error::"*"ghcr.io/o/r/slim:t"* ]] || { echo "step $step: $output"; return 1; }
  done
}

@test "a failing docker call names its step" {
  STUB_FAIL=hctimes run bash "$SCRIPT" slim ref:t
  [ "$status" -eq 1 ]
  [[ "$output" == *"::error::"*"HEALTHCHECK timings"*"ref:t"* ]]
  STUB_FAIL=run run bash "$SCRIPT" slim ref:t
  [[ "$output" == *"::error::"*"docker run"*"ref:t"* ]]
  STUB_FAIL='create' run bash "$SCRIPT" slim ref:t
  [[ "$output" == *"::error::"*"docker create"*"ref:t"* ]]
  STUB_FAIL='export' run bash "$SCRIPT" slim ref:t
  [[ "$output" == *"::error::"*"docker export"*"ref:t"* ]]
}

@test "a failing docker call does not stop the other checks" {
  STUB_FAIL=user run bash "$SCRIPT" slim ref:t
  [[ "$output" == *"healthcheck: ok"* ]]
  [[ "$output" == *"labels: ok"* ]]
}

@test "a failing docker call still removes the container" {
  STUB_FAIL=state run bash "$SCRIPT" slim ref:t
  name="$(sed -n 's/^run .*--name \([^ ]*\) .*/\1/p' "$STUB_LOG")"
  grep -qx "rm -f $name" "$STUB_LOG"
}
