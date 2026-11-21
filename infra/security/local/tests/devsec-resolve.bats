#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# devsec-resolve.bats — unit tests for devsec.sh's local build-coordinate
# resolution: load_local_env (safe parse, never source), resolve_registry
# (.env.local override vs git-remote derive), resolve_project_slug (override vs
# git-slug fallback), and local_image_tag (deterministic per commit).
#
# TDD: written first and run RED (resolvers undefined), then GREEN once they
# land. devsec.sh is source-safe, so we `source` it and call the resolvers
# directly rather than shelling out per case; a stub `git` on PATH (the same
# helper as devsec-env.bats) supplies deterministic CI_* derivation. Each case
# runs in its own `bash -c` so module state never bleeds between cases.
# -----------------------------------------------------------------------------

setup() {
  # $BATS_TEST_DIRNAME is this file's dir (.../tests); devsec.sh is its parent.
  # Absolute path, no "..", so devsec.sh's own dirname(dirname(BASH_SOURCE[0]))
  # helper-resolution lands in infra/security/ (not in tests/).
  SCRIPT="$(dirname "$BATS_TEST_DIRNAME")/devsec.sh"
}

# _setup_git [SHA SHORT REF REMOTE] — put a stub `git` on PATH returning fixed
# values (defaults are the brief's values). PATH is exported so the stub is also
# visible to the later `run bash -c` block, whose derive_ci_env would otherwise
# call the real git.
_setup_git() {
  local sha="${1:-abc123def456}" short="${2:-abc123d}" ref="${3:-feature/x}"
  # ${4-...} (no colon): an explicit empty arg must stay empty (the no-remote
  # case), while a truly omitted arg falls back to the default.
  local remote="${4-https://github.com/acme/service-template.git}"
  mkdir -p "$BATS_TEST_TMPDIR/bin"
  cat > "$BATS_TEST_TMPDIR/bin/git" <<EOF
#!/usr/bin/env bash
cmd="\$1"; shift
case "\$cmd|\$*" in
  "rev-parse|HEAD") echo "${sha}" ;;
  "rev-parse|--short HEAD") echo "${short}" ;;
  "rev-parse|--abbrev-ref HEAD") echo "${ref}" ;;
  "config|--get remote.origin.url") echo "${remote}" ;;
esac
EOF
  chmod +x "$BATS_TEST_TMPDIR/bin/git"
  export PATH="$BATS_TEST_TMPDIR/bin:$PATH"
}

# RED anchor: with the resolvers not yet implemented, sourcing devsec.sh and
# calling resolve_registry dies with "command not found", so the resolved value
# is empty and the assertion fails.
@test "resolve_registry prefers the .env.local REGISTRY override" {
  _setup_git "abc123def456" "abc123d" "main" "https://github.com/acme/service-template.git"
  local envfile="$BATS_TEST_TMPDIR/.env.local"
  printf 'REGISTRY=registry.example.com:5050\n' > "$envfile"
  run bash -c "
    source '$SCRIPT' >/dev/null 2>&1
    derive_ci_env
    load_local_env '$envfile'
    resolve_registry
    printf '%s\n' \"\$LOCAL_REGISTRY\"
  "
  [ "$output" = "registry.example.com:5050" ]
}

@test "resolve_registry defaults to ghcr.io whatever the git remote" {
  _setup_git "abc123def456" "abc123d" "main" "https://github.com/acme/service-template.git"
  run bash -c "
    source '$SCRIPT' >/dev/null 2>&1
    derive_ci_env
    load_local_env '$BATS_TEST_TMPDIR/absent.env.local'
    resolve_registry
    printf '%s\n' \"\$LOCAL_REGISTRY\"
  "
  [ "$output" = "ghcr.io" ]
}

@test "local_image_tag is deterministic for a given SHA" {
  run bash -c "
    source '$SCRIPT' >/dev/null 2>&1
    GIT_SHORT_SHA=abc123d
    printf '%s %s\n' \"\$(local_image_tag)\" \"\$(local_image_tag)\"
  "
  [ "$output" = "local-abc123d local-abc123d" ]
}

@test "local_image_tag falls back to local-dev with no commit available" {
  run bash -c "
    source '$SCRIPT' >/dev/null 2>&1
    GIT_SHORT_SHA=
    printf '%s\n' \"\$(local_image_tag)\"
  "
  [ "$output" = "local-dev" ]
}

@test "LOCAL_PROJECT is the lowercased GITHUB_REPOSITORY without a .env.local override" {
  _setup_git "abc123def456" "abc123d" "main" "https://github.com/Acme/Service-Template.git"
  run bash -c "
    source '$SCRIPT' >/dev/null 2>&1
    derive_ci_env
    load_local_env '$BATS_TEST_TMPDIR/absent.env.local'
    resolve_project_slug
    printf '%s\n' \"\$LOCAL_PROJECT\"
  "
  [ "$output" = "acme/service-template" ]
}

@test "LOCAL_PROJECT honours the .env.local GITHUB_REPOSITORY override, lowercased" {
  _setup_git "abc123def456" "abc123d" "main" "https://github.com/acme/service-template.git"
  local envfile="$BATS_TEST_TMPDIR/.env.local"
  printf 'GITHUB_REPOSITORY=Team/Other-Project\n' > "$envfile"
  run bash -c "
    source '$SCRIPT' >/dev/null 2>&1
    derive_ci_env
    load_local_env '$envfile'
    resolve_project_slug
    printf '%s\n' \"\$LOCAL_PROJECT\"
  "
  [ "$output" = "team/other-project" ]
}
