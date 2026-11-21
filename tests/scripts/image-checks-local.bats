#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# image-checks-local.bats — scripts/ci/image-checks-local.sh (make image-checks) against a stub docker:
# it builds with the manifest's own build arguments (the way ci.yml does), checks strictly, removes the images.
# -----------------------------------------------------------------------------

setup() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
  SCRIPT="$ROOT/scripts/ci/image-checks-local.sh"
  export STUB_LOG="$BATS_TEST_TMPDIR/docker.log"
  : > "$STUB_LOG"
  export IMAGE_CHECKS_POLL=0.05
  export IMAGE_CHECKS_MANIFEST="$BATS_TEST_TMPDIR/images.yml"
  cat > "$IMAGE_CHECKS_MANIFEST" <<'YML'
schema_version: 1
images:
  - id: slim
    repository: "ghcr.io/${OWNER}/${REPO}/slim"
    dockerfile: Dockerfile.slim
    context: ctx
    distroless: true
    build_args:
      VERSION: "${VERSION}"
      EXTRA: "from-manifest"
  - id: full
    repository: "ghcr.io/${OWNER}/${REPO}/full"
    dockerfile: Dockerfile.full
    distroless: false
YML
  mkdir -p "$BATS_TEST_TMPDIR/bin"
  cp "$BATS_TEST_DIRNAME/fixtures/docker-stub.sh" "$BATS_TEST_TMPDIR/bin/docker"
  PATH="$BATS_TEST_TMPDIR/bin:$PATH"
}

@test "the build uses the manifest's build arguments, expanded, plus the label values" {
  run bash "$SCRIPT" slim
  [ "$status" -eq 0 ]
  line="$(grep '^buildx build' "$STUB_LOG")"
  [[ "$line" == *"--build-arg EXTRA=from-manifest"* ]]
  [[ "$line" == *"--build-arg VERSION=0.0.0"* ]]
  [[ "$line" == *"--build-arg SOURCE_URL="* ]]
  [[ "$line" == *"--build-arg VCS_REF="* ]]
  [[ "$line" == *"--build-arg BUILD_DATE="* ]]
  [[ "$line" == *"--file Dockerfile.slim"* ]]
  [[ "$line" == *" ctx" ]]
}

@test "every image of the manifest is built, checked strictly and removed by its exact tag" {
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"image-checks: slim"*"[strict]"* ]]
  [[ "$output" == *"image-checks: full"*"[strict]"* ]]
  [ "$(grep -c '^buildx build' "$STUB_LOG")" -eq 2 ]
  for id in slim full; do
    tag="$(grep '^buildx build' "$STUB_LOG" | sed -n "s/.*--tag \\(image-checks-local\\/$id:[^ ]*\\).*/\\1/p")"
    [ -n "$tag" ]
    grep -qx "rmi $tag" "$STUB_LOG"
  done
}

@test "an unknown image id fails" {
  run bash "$SCRIPT" nope
  [ "$status" -ne 0 ]
  [[ "$output" == *"::error::"*"nope"* ]]
}

@test "a failing check makes the run fail, and the image is still removed" {
  STUB_USER=0 run bash "$SCRIPT" slim
  [ "$status" -ne 0 ]
  grep -q '^rmi image-checks-local/slim:' "$STUB_LOG"
}
