#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# devsec-scanenv.bats — unit tests for devsec.sh's local image build + scan.env
# generation: build_images (read id/dockerfile/context from
# container-images.yml via yq, guard root + the docker daemon, build+tag each id,
# skip+warn an id whose Dockerfile is absent and continue with the rest) and the
# pure write_scan_env (id->repo:tag map -> dotenv text with <ID>_IMAGE /
# <ID>_IMAGE_DOCKER / IMAGE_TAG, re-parseable by safe_load_env).
#
# TDD: written first and run RED (build_images / write_scan_env undefined), then
# GREEN once they land. build_images mutates the system, so — like devsec-tools.bats
# — we drive it via `bash -c` with a controlled PATH: a stub `docker` on PATH
# RECORDS its argv (so the test never builds a real image) and answers `docker info`
# (need_docker_daemon) as reachable; yq is the REAL one reading a per-test fixture
# container-images.yml (CONTAINER_IMAGES_YML), so each case controls exactly which
# Dockerfile is present vs missing. write_scan_env is pure, so we source and call it
# directly into a temp file, then re-parse with safe_load_env. Each case runs in its
# own `bash -c`, so module state (the _BUILT_IMAGES map, PATH) never bleeds.
# -----------------------------------------------------------------------------

setup() {
  # $BATS_TEST_DIRNAME is this file's dir (.../tests); devsec.sh is its parent.
  # Absolute path, no "..", so devsec.sh's own dirname(dirname(BASH_SOURCE[0]))
  # helper-resolution lands in infra/security/ (not in tests/).
  SCRIPT="$(dirname "$BATS_TEST_DIRNAME")/devsec.sh"
}

# _setup_docker — put a stub `docker` on PATH (prepended, so the real yq/jq/...
# stay reachable): `docker info` reports the daemon reachable (satisfies
# need_docker_daemon); `docker build ...` appends its argv to docker.log and
# builds nothing. Exports PATH so the `bash -c` subprocess sees the stub.
_setup_docker() {
  local bindir="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$bindir"
  : > "$BATS_TEST_TMPDIR/docker.log"
  cat > "$bindir/docker" <<'EOF'
#!/usr/bin/env bash
case "$1" in
  info) exit 0 ;;
  build) printf 'docker %s\n' "$*" >> "$BATS_TEST_TMPDIR/docker.log" ;;
  *) exit 0 ;;
esac
EOF
  chmod +x "$bindir/docker"
  export PATH="$bindir:$PATH"
}

# _write_image_fixture CONTEXT_DOCKERFILES... — write a per-test container-images.yml
# (ids backend + nginx, context ".", the dockerfiles named) and create exactly the
# named Dockerfiles in a fresh work dir, so build_images sees those present and the
# rest missing. Prints the work dir (the context ".") on stdout.
_write_image_fixture() {
  local work="$BATS_TEST_TMPDIR/work"
  mkdir -p "$work"
  local f
  for f in "$@"; do : > "$work/$f"; done
  cat > "$BATS_TEST_TMPDIR/images.yml" <<'EOF'
images:
  - id: backend
    dockerfile: Dockerfile.backend
    context: .
  - id: nginx
    dockerfile: Dockerfile.nginx
    context: .
EOF
  printf '%s' "$work"
}

# RED anchor: with build_images / write_scan_env not yet implemented, sourcing
# devsec.sh and calling them dies with "command not found", so status is non-zero
# and docker.log stays empty — both assertions fail until they are implemented.

@test "write_scan_env emits IMAGE + IMAGE_DOCKER per id, parseable by safe_load_env" {
  run bash -c "
    source '$SCRIPT' >/dev/null 2>&1
    declare -A M=( [backend]='ghcr.io/acme/svc/backend:local-abc' [nginx]='ghcr.io/acme/svc/nginx:local-abc' )
    LOCAL_TAG='local-abc'
    write_scan_env M '$BATS_TEST_TMPDIR/scan.env'
  "
  [ "$status" -eq 0 ]
  [ -f "$BATS_TEST_TMPDIR/scan.env" ]
  # Re-parse exactly as a downstream scanner would, and assert the keys/values.
  run bash -c "
    source '$SCRIPT' >/dev/null 2>&1
    declare -A E=()
    safe_load_env '$BATS_TEST_TMPDIR/scan.env' E
    printf 'BACKEND_IMAGE=%s\n' \"\${E[BACKEND_IMAGE]}\"
    printf 'BACKEND_IMAGE_DOCKER=%s\n' \"\${E[BACKEND_IMAGE_DOCKER]}\"
    printf 'NGINX_IMAGE=%s\n' \"\${E[NGINX_IMAGE]}\"
    printf 'NGINX_IMAGE_DOCKER=%s\n' \"\${E[NGINX_IMAGE_DOCKER]}\"
    printf 'IMAGE_TAG=%s\n' \"\${E[IMAGE_TAG]}\"
  "
  [ "$output" = "BACKEND_IMAGE=ghcr.io/acme/svc/backend:local-abc
BACKEND_IMAGE_DOCKER=docker:ghcr.io/acme/svc/backend:local-abc
NGINX_IMAGE=ghcr.io/acme/svc/nginx:local-abc
NGINX_IMAGE_DOCKER=docker:ghcr.io/acme/svc/nginx:local-abc
IMAGE_TAG=local-abc" ]
}

@test "build_images builds each id with its dockerfile/context from container-images.yml" {
  _setup_docker
  local work; work="$(_write_image_fixture Dockerfile.backend Dockerfile.nginx)"
  run bash -c "
    source '$SCRIPT' >/dev/null 2>&1
    LOCAL_IMAGE_REPO='ghcr.io/acme/svc'
    LOCAL_TAG='local-abc'
    ALLOW_ROOT=1
    CONTAINER_IMAGES_YML='$BATS_TEST_TMPDIR/images.yml'
    cd '$work'
    build_images
    _ks=()
    mapfile -t _ks < <(printf '%s\n' \"\${!_BUILT_IMAGES[@]}\" | LC_ALL=C sort)
    echo \"MAP:\${_ks[*]}\"
  "
  [ "$status" -eq 0 ]
  # docker build invoked once per id, with the right -f / -t / context from the yml.
  [ "$(cat "$BATS_TEST_TMPDIR/docker.log")" = "docker build -f Dockerfile.backend -t ghcr.io/acme/svc/backend:local-abc .
docker build -f Dockerfile.nginx -t ghcr.io/acme/svc/nginx:local-abc ." ]
  # ...and both ids were collected into the map write_scan_env consumes.
  [[ "$output" == *"MAP:backend nginx"* ]]
}

@test "build_images skips an id whose dockerfile is missing, warns, continues" {
  _setup_docker
  # Only backend's Dockerfile exists; nginx's is absent.
  local work; work="$(_write_image_fixture Dockerfile.backend)"
  run bash -c "
    source '$SCRIPT' >/dev/null 2>&1
    LOCAL_IMAGE_REPO='ghcr.io/acme/svc'
    LOCAL_TAG='local-abc'
    ALLOW_ROOT=1
    CONTAINER_IMAGES_YML='$BATS_TEST_TMPDIR/images.yml'
    cd '$work'
    build_images
    _ks=()
    mapfile -t _ks < <(printf '%s\n' \"\${!_BUILT_IMAGES[@]}\" | LC_ALL=C sort)
    echo \"MAP:\${_ks[*]}\"
  "
  # Did NOT die on the missing Dockerfile — the chain continues (exit 0).
  [ "$status" -eq 0 ]
  # Only backend was built; nginx was skipped.
  [ "$(cat "$BATS_TEST_TMPDIR/docker.log")" = "docker build -f Dockerfile.backend -t ghcr.io/acme/svc/backend:local-abc ." ]
  [[ "$output" == *"MAP:backend"* ]]
  # A warning naming the skipped image was emitted (on stderr, captured by run).
  [[ "$output" == *"nginx"* ]]
}
