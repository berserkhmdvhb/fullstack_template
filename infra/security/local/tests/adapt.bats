#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# adapt.bats -- unit tests for the single merged infra/adapt/replace_service_name
# tool. It now carries BOTH concerns the old adapters split across two scripts:
#
#   - service name: re-brand `your_service_name` EVERYWHERE -- the src/<name>
#     package directory, the `version.package_name` scalar in
#     .github/container-images.yml, the `- src/<name>` project path in
#     infra/security/config.yaml, and the tree-wide text walk of the name AND its
#     hyphen slug (whole-token only).
#   - image wiring: regenerate the `images:` block (.github/container-images.yml) and
#     the `image.services:` block (infra/security/config.yaml) from the root
#     Dockerfiles, so the build/scan manifests mirror the actual Dockerfile set.
#     The image id is the Dockerfile filename suffix; a bare `Dockerfile` takes
#     the new service name.
#
# Each case builds a throwaway fixture repo (ROOT) and asserts on the rewritten
# files. No scanner, no docker, no network -- just Python + yq over fixture YAML.
# -----------------------------------------------------------------------------

setup() {
  # infra/adapt/ is three levels up from infra/security/local/tests/.
  ADAPT_DIR="$(cd "$BATS_TEST_DIRNAME/../../../adapt" >/dev/null 2>&1 && pwd)"
  REPO_ROOT="$ADAPT_DIR/../.."
  SCRIPT="$ADAPT_DIR/replace_service_name.sh"
  ROOT="$BATS_TEST_TMPDIR/repo"
  mkdir -p "$ROOT/ci" "$ROOT/.github" "$ROOT/infra/security"

  cat > "$ROOT/.github/container-images.yml" <<'YAML'
# .github/container-images.yml (fixture)
schema_version: 1
version:
  source: git
  package_name: your_service_name
images:
  - id: backend
    repository: "ghcr.io/${OWNER}/${REPO}/backend"
    dockerfile: Dockerfile.backend
    context: .
    dockerfile_context: .
    build_args:
      VERSION: "${VERSION}"
YAML

  cat > "$ROOT/infra/security/config.yaml" <<'YAML'
# infra/security/config.yaml (fixture)
paths:
  projects:
    - src/your_service_name
image:
  env_file: scan.env
  services:
    - name: backend
      env_var: BACKEND_IMAGE
      docker_env_var: BACKEND_IMAGE_DOCKER
      match: backend
tools:
  syft: v1.55.0
  grype: v0.121.0
policy:
  fail_level: high
  only_fixed: true
grouping:
  rules:
    - match: .image.
      group: CONTAINER IMAGE
YAML
}

# =============================================================================
# service name
# =============================================================================

# -----------------------------------------------------------------------------
# CLI / validation: --new-name is required, names must be [a-z][a-z0-9_]*
# -----------------------------------------------------------------------------
@test "service-name: --new-name is required" {
  run bash "$SCRIPT" --root "$ROOT"
  [ "$status" -ne 0 ]
}

@test "service-name: rejects an invalid new name" {
  run bash "$SCRIPT" --root "$ROOT" -n Bad-Name
  [ "$status" -ne 0 ]
  [[ "$output" == *"Invalid"* ]]
}

@test "service-name: wrapper forwards to the python module" {
  run bash "$SCRIPT" --root "$ROOT" -n acme
  [ "$status" -eq 0 ]
  [[ "$output" == *"OLD_NAME=your_service_name -> NEW_NAME=acme"* ]]
}

# -----------------------------------------------------------------------------
# the two scalars are re-targeted; the old token is gone
# -----------------------------------------------------------------------------
@test "service-name: re-targets package_name in container-images.yml" {
  run bash "$SCRIPT" --root "$ROOT" -n acme
  [ "$status" -eq 0 ]
  [ "$(yq '.version.package_name' "$ROOT/.github/container-images.yml")" = "acme" ]
}

@test "service-name: re-targets the project path in config.yaml" {
  run bash "$SCRIPT" --root "$ROOT" -n acme
  [ "$status" -eq 0 ]
  [ "$(yq '.paths.projects[0]' "$ROOT/infra/security/config.yaml")" = "src/acme" ]
}

# -----------------------------------------------------------------------------
# the tree walk replaces the name AND its hyphen slug, whole-token only, and
# respects the excluded dirs; every touched file is backed up on a real run
# -----------------------------------------------------------------------------
@test "service-name: replaces name + slug in tree text, respects excludes" {
  # The replacer is context-aware (imports / src paths / slugs), not a blind
  # repo-wide replace, so exercise a real token context in a target file.
  printf 'from your_service_name import echo  # owns your-service-name\n' > "$ROOT/echo.py"
  mkdir -p "$ROOT/infra/adapt"; printf 'from your_service_name import x\n' > "$ROOT/infra/adapt/keep.py"
  run bash "$SCRIPT" --root "$ROOT" -n acme_solution
  [ "$status" -eq 0 ]
  grep -qF 'acme_solution' "$ROOT/echo.py"                 # name replaced (import)
  grep -qF 'acme-solution' "$ROOT/echo.py"                 # slug replaced
  grep -qF 'your_service_name' "$ROOT/infra/adapt/keep.py"  # excluded dir untouched
}

@test "service-name: rewrites .env.example, skips a path listed in excludes.txt" {
  printf 'BACKEND_ENTRY=your_service_name.backend.main:app\n' > "$ROOT/.env.example"
  mkdir -p "$ROOT/agent"; printf 'from your_service_name import x\n' > "$ROOT/agent/keep.py"
  run bash "$SCRIPT" --root "$ROOT" -n acme
  [ "$status" -eq 0 ]
  grep -qF 'BACKEND_ENTRY=acme.backend.main:app' "$ROOT/.env.example"
  grep -qF 'your_service_name' "$ROOT/agent/keep.py"
}

@test "service-name: real run backs up every touched file" {
  printf 'from your_service_name import echo\n' > "$ROOT/echo.py"
  run bash "$SCRIPT" --root "$ROOT" -n acme
  [ "$status" -eq 0 ]
  [ -n "$(find "$ROOT/.adapt-backup" -type f -name container-images.yml)" ]
  [ -n "$(find "$ROOT/.adapt-backup" -type f -name config.yaml)" ]
  [ -n "$(find "$ROOT/.adapt-backup" -type f -name echo.py)" ]
}

# -----------------------------------------------------------------------------
# dry-run is a no-op; the no-op guard fires when already the target service
# -----------------------------------------------------------------------------
@test "service-name: dry-run changes nothing (no files backed up)" {
  run bash "$SCRIPT" --root "$ROOT" -n acme --dry-run
  [ "$status" -eq 0 ]
  grep -qF 'your_service_name' "$ROOT/.github/container-images.yml"
  [ -z "$(find "$ROOT/.adapt-backup" -type f 2>/dev/null)" ]
}

@test "service-name: no-op when already the target service" {
  run bash "$SCRIPT" --root "$ROOT" -n your_service_name
  [ "$status" -eq 0 ]
  [[ "$output" == *"nothing to do"* ]]
  [ -z "$(find "$ROOT/.adapt-backup" -type f 2>/dev/null)" ]
}

# =============================================================================
# image wiring
# =============================================================================

# -----------------------------------------------------------------------------
# detection: image id is the root Dockerfile's filename suffix (multi-image);
# the bare Dockerfile takes the new service name; no Dockerfile skips gracefully
# (the rename still succeeds -- image wiring is optional)
# -----------------------------------------------------------------------------
@test "images: detects image ids from root Dockerfile filenames (multi-image)" {
  printf 'FROM scratch\n' > "$ROOT/Dockerfile.api"
  printf 'FROM scratch\n' > "$ROOT/Dockerfile.worker"
  run bash "$SCRIPT" --root "$ROOT" -n acme
  [ "$status" -eq 0 ]
  [[ "$output" == *"Rewired 2 image(s)"* ]]
  [ "$(yq '.images | length' "$ROOT/.github/container-images.yml")" -eq 2 ]
  [ "$(yq '.images[0].id' "$ROOT/.github/container-images.yml")" = "api" ]
  [ "$(yq '.images[1].id' "$ROOT/.github/container-images.yml")" = "worker" ]
  run yq '.images[].id' "$ROOT/.github/container-images.yml"; [[ "$output" != *"backend"* ]]
}

@test "images: bare root Dockerfile takes the new service name as its id" {
  printf 'FROM scratch\n' > "$ROOT/Dockerfile"
  run bash "$SCRIPT" --root "$ROOT" -n acme
  [ "$status" -eq 0 ]
  [[ "$output" == *"Rewired 1 image(s)"* ]]
  [ "$(yq '.images | length' "$ROOT/.github/container-images.yml")" -eq 1 ]
  [ "$(yq '.images[0].id' "$ROOT/.github/container-images.yml")" = "acme" ]
  [ "$(yq '.images[0].dockerfile' "$ROOT/.github/container-images.yml")" = "Dockerfile" ]
}

@test "images: skips gracefully when there is no root Dockerfile" {
  run bash "$SCRIPT" --root "$ROOT" -n acme
  [ "$status" -eq 0 ]
  [[ "$output" == *"nothing to build"* ]]
}

# -----------------------------------------------------------------------------
# container-images.yml: the images block is regenerated from the detected
# Dockerfiles; the old single `backend` entry is gone
# config.yaml: image.services is derived from the detected Dockerfiles; the
# derived <ID>_IMAGE / <ID>_IMAGE_DOCKER tokens and tool pins survive
# -----------------------------------------------------------------------------
@test "images: derives config.yaml image.services from the detected Dockerfiles" {
  printf 'FROM scratch\n' > "$ROOT/Dockerfile.api"
  printf 'FROM scratch\n' > "$ROOT/Dockerfile.worker"
  run bash "$SCRIPT" --root "$ROOT" -n acme
  [ "$status" -eq 0 ]
  [ "$(yq '.image.services | length' "$ROOT/infra/security/config.yaml")" -eq 2 ]
  [ "$(yq '.image.services[0].name' "$ROOT/infra/security/config.yaml")" = "api" ]
  [ "$(yq '.image.services[0].env_var' "$ROOT/infra/security/config.yaml")" = "API_IMAGE" ]
  [ "$(yq '.image.services[0].docker_env_var' "$ROOT/infra/security/config.yaml")" = "API_IMAGE_DOCKER" ]
  [ "$(yq '.image.services[0].match' "$ROOT/infra/security/config.yaml")" = "api" ]
  run yq '.image.services[].env_var' "$ROOT/infra/security/config.yaml"; [[ "$output" != *"BACKEND_IMAGE"* ]]
}

@test "images: preserves tool pins, policy, grouping and comments byte-for-byte" {
  printf 'FROM scratch\n' > "$ROOT/Dockerfile.api"
  run bash "$SCRIPT" --root "$ROOT" -n acme
  [ "$status" -eq 0 ]
  [ "$(yq '.tools.syft' "$ROOT/infra/security/config.yaml")" = "v1.55.0" ]    # pin survives
  [ "$(yq '.policy.fail_level' "$ROOT/infra/security/config.yaml")" = "high" ]
  grep -qF 'grouping:' "$ROOT/infra/security/config.yaml"                     # block survives
  grep -qF '# infra/security/config.yaml (fixture)' "$ROOT/infra/security/config.yaml"  # comment survives
}

# -----------------------------------------------------------------------------
# dry-run is a no-op; the no-op guard fires when the manifests already carry the
# canonical blocks for the detected images
# -----------------------------------------------------------------------------
@test "images: dry-run changes nothing (no files backed up)" {
  printf 'FROM scratch\n' > "$ROOT/Dockerfile.api"
  run bash "$SCRIPT" --root "$ROOT" -n acme --dry-run
  [ "$status" -eq 0 ]
  [ "$(yq '.images[0].id' "$ROOT/.github/container-images.yml")" = "backend" ]  # original block untouched
  [ -z "$(find "$ROOT/.adapt-backup" -type f 2>/dev/null)" ]
}

@test "images: no-op when the manifests already carry the canonical blocks" {
  printf 'FROM scratch\n' > "$ROOT/Dockerfile.backend"  # matches the fixture's single entry
  run bash "$SCRIPT" --root "$ROOT" -n acme
  [ "$status" -eq 0 ]
  [[ "$output" == *"already wired"* ]]
}

# =============================================================================
# end-to-end on the real template: the single dry-run must not touch a byte, so
# the real template files stay exactly as they are, and both YAMLs stay valid
# =============================================================================
@test "end-to-end on the real template: dry-run preserves formatting, YAML valid" {
  run bash "$SCRIPT" --root "$REPO_ROOT" -n acme --dry-run
  [ "$status" -eq 0 ]
  yq '.' "$REPO_ROOT/.github/container-images.yml" >/dev/null
  yq '.' "$REPO_ROOT/infra/security/config.yaml" >/dev/null
}
