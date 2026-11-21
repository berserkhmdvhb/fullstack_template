#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# oasdiff.bats — scripts/ci/oasdiff.sh: a breaking change fails without the label and passes with it, a
# compatible change passes, a push skips, and the pull-request path reads the base snapshot with git show.
# Needs the pinned oasdiff (scripts/tools.sh install oasdiff).
# -----------------------------------------------------------------------------

setup() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
  SCRIPT="$ROOT/scripts/ci/oasdiff.sh"
  FIX="$BATS_TEST_DIRNAME/fixtures"
  unset BREAKING_CHANGE_LABEL EVENT_NAME BASE_REF BASE_REV GITHUB_STEP_SUMMARY
}

@test "a removed endpoint fails without the label" {
  run bash "$SCRIPT" "$FIX/openapi-base.json" "$FIX/openapi-breaking.json"
  [ "$status" -eq 1 ]
  [[ "$output" == *"api-path-removed-without-deprecation"* ]]
  [[ "$output" == *"breaking-change label"* ]]
}

@test "a removed endpoint passes with the breaking-change label" {
  BREAKING_CHANGE_LABEL=true run bash "$SCRIPT" "$FIX/openapi-base.json" "$FIX/openapi-breaking.json"
  [ "$status" -eq 0 ]
  [[ "$output" == *"api-path-removed-without-deprecation"* ]]
}

@test "any other label value does not allow the change" {
  BREAKING_CHANGE_LABEL=false run bash "$SCRIPT" "$FIX/openapi-base.json" "$FIX/openapi-breaking.json"
  [ "$status" -eq 1 ]
}

@test "an added endpoint passes" {
  run bash "$SCRIPT" "$FIX/openapi-base.json" "$FIX/openapi-compatible.json"
  [ "$status" -eq 0 ]
  [[ "$output" == *"no breaking change"* ]]
}

@test "a push skips with a summary line" {
  EVENT_NAME=push run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"skipped"* ]]
}

# The pull-request path: the base snapshot comes from git (BASE_REV here; BASE_REF is fetched in CI).
make_repo() {
  R="$BATS_TEST_TMPDIR/repo"
  mkdir -p "$R/scripts/ci" "$R/frontend/lib/api"
  cp "$SCRIPT" "$R/scripts/ci/oasdiff.sh"
  git -C "$R" init -q
  git -C "$R" config user.email t@example.invalid
  git -C "$R" config user.name t
  cp "$FIX/openapi-base.json" "$R/frontend/lib/api/openapi.json"
  git -C "$R" add -A
  git -C "$R" commit -q -m base
  git -C "$R" tag base
}

@test "pull request: the snapshot is compared with the base revision's" {
  make_repo
  cp "$FIX/openapi-breaking.json" "$R/frontend/lib/api/openapi.json"
  PATH="$ROOT/.tools/bin:$PATH" BASE_REV=base run bash "$R/scripts/ci/oasdiff.sh"
  [ "$status" -eq 1 ]
  PATH="$ROOT/.tools/bin:$PATH" BASE_REV=base BREAKING_CHANGE_LABEL=true run bash "$R/scripts/ci/oasdiff.sh"
  [ "$status" -eq 0 ]
  cp "$FIX/openapi-compatible.json" "$R/frontend/lib/api/openapi.json"
  PATH="$ROOT/.tools/bin:$PATH" BASE_REV=base run bash "$R/scripts/ci/oasdiff.sh"
  [ "$status" -eq 0 ]
}

@test "pull request: BASE_REF is fetched from origin" {
  make_repo
  git clone -q --bare "$R" "$BATS_TEST_TMPDIR/origin.git"
  git -C "$R" remote add origin "$BATS_TEST_TMPDIR/origin.git"
  git -C "$R" branch -M main
  git -C "$R" push -q origin main
  cp "$FIX/openapi-breaking.json" "$R/frontend/lib/api/openapi.json"
  PATH="$ROOT/.tools/bin:$PATH" BASE_REF=main run bash "$R/scripts/ci/oasdiff.sh"
  [ "$status" -eq 1 ]
}

@test "pull request: no base snapshot yet skips" {
  make_repo
  git -C "$R" rm -q --cached frontend/lib/api/openapi.json
  git -C "$R" commit -q -m drop
  git -C "$R" tag nosnap
  PATH="$ROOT/.tools/bin:$PATH" BASE_REV=nosnap run bash "$R/scripts/ci/oasdiff.sh"
  [ "$status" -eq 0 ]
  [[ "$output" == *"no frontend/lib/api/openapi.json yet"* ]]
}
