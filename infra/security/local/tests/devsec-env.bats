#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# devsec-env.bats — unit tests for devsec.sh's pure git->CI env derivation.
#
# TDD: written first and run RED (functions undefined), then GREEN once
# derive_ci_env / _selftest_env landed. `devsec.sh` is source-safe, so we
# `source` it to call derive_ci_env directly rather than shelling out per case.
# -----------------------------------------------------------------------------

setup() {
  # $BATS_TEST_DIRNAME is bats' absolute path to this file's dir (.../tests);
  # devsec.sh is its parent dir. Build an absolute path with no ".." so the
  # script's own `dirname(dirname(BASH_SOURCE[0]))` helper-resolution lands in
  # infra/security/ (a literal ".." in the path would make it land in tests/).
  SCRIPT="$(dirname "$BATS_TEST_DIRNAME")/devsec.sh"
}

# _setup_git [SHA SHORT REF REMOTE] — put a stub `git` on PATH returning the
# given fixed values (defaults are the brief's values). Case patterns are
# QUOTED (a case pattern may not begin with a bare '-') and matched on
# "$1|$*" after `shift`, so unset trailing args leave no stray space. The
# brief's `case "$2 $3"` stub both syntax-errors on `--short` and mismatches
# `HEAD` on that trailing space, so it is corrected here. PATH is exported
# (not an inline prefix) so the stub is also visible to the later `run bash -c`
# block, whose `derive_ci_env` would otherwise call the real git.
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

@test "derive_ci_env maps git HEAD to GITHUB_SHA/GITHUB_REF_NAME and a short sha" {
  _setup_git
  run bash -c "source '$SCRIPT' >/dev/null 2>&1; derive_ci_env; echo \$GITHUB_SHA \$GIT_SHORT_SHA \$GITHUB_REF_NAME"
  [ "$output" = "abc123def456 abc123d feature/x" ]
}

@test "derive_ci_env derives GITHUB_REPOSITORY and GITHUB_SERVER_URL from the remote" {
  _setup_git "abc123def456" "abc123d" "main" "https://github.com/Acme/Service-Template.git"
  run bash -c "source '$SCRIPT' >/dev/null 2>&1; derive_ci_env; printf '%s|%s\n' \"\$GITHUB_REPOSITORY\" \"\$GITHUB_SERVER_URL\""
  [ "$output" = "Acme/Service-Template|https://github.com" ]
}

@test "derive_ci_env handles an scp-like ssh remote" {
  _setup_git "abc123def456" "abc123d" "main" "git@github.com:acme/svc.git"
  run bash -c "source '$SCRIPT' >/dev/null 2>&1; derive_ci_env; printf '%s|%s\n' \"\$GITHUB_REPOSITORY\" \"\$GITHUB_SERVER_URL\""
  [ "$output" = "acme/svc|https://github.com" ]
}

@test "derive_ci_env falls back to sentinels when the repo has no remote" {
  _setup_git "deadbeefcafe" "deadbeef" "main" ""
  run bash -c "source '$SCRIPT' >/dev/null 2>&1; derive_ci_env; printf '%s|%s\n' \"\$GITHUB_REPOSITORY\" \"\$GITHUB_SERVER_URL\""
  [ "$output" = "unknown|https://localhost" ]
}

@test "derive_ci_env sets no CI_* variable" {
  _setup_git
  run bash -c "source '$SCRIPT' >/dev/null 2>&1; derive_ci_env; env | grep -c '^CI_' || true"
  [ "$output" = "0" ]
}
