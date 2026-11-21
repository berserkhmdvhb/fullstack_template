#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# oasdiff.bats — scripts/ci/oasdiff.sh: a breaking change fails without the label and passes with it, a
# compatible change passes, a push skips, a spec that does not load fails whatever the label says, and the
# pull-request path reads the base snapshot from the first parent of the merge commit.
# Needs the pinned oasdiff (scripts/tools.sh install oasdiff).
# -----------------------------------------------------------------------------

setup() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
  SCRIPT="$ROOT/scripts/ci/oasdiff.sh"
  FIX="$BATS_TEST_DIRNAME/fixtures"
  unset BREAKING_CHANGE_LABEL EVENT_NAME GITHUB_STEP_SUMMARY
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

@test "a spec that does not load fails, label or not" {
  echo '{bad' > "$BATS_TEST_TMPDIR/bad.json"
  run bash "$SCRIPT" "$FIX/openapi-base.json" "$BATS_TEST_TMPDIR/bad.json"
  [ "$status" -ne 0 ]
  BREAKING_CHANGE_LABEL=true run bash "$SCRIPT" "$FIX/openapi-base.json" "$BATS_TEST_TMPDIR/bad.json"
  [ "$status" -ne 0 ]
  [[ "$output" == *"label does not apply"* ]]
}

# The pull-request path: HEAD is a merge commit whose first parent is the base (what actions/checkout tests).
# merge_with SNAPSHOT: branch `base` holds the base snapshot, branch `pr` holds SNAPSHOT, HEAD merges pr into base.
merge_with() {
  R="$BATS_TEST_TMPDIR/repo"
  mkdir -p "$R/scripts/ci" "$R/frontend/lib/api"
  cp "$SCRIPT" "$R/scripts/ci/oasdiff.sh"
  git -C "$R" init -q -b base
  git -C "$R" config user.email t@example.invalid
  git -C "$R" config user.name t
  if [ -z "${NO_BASE_SNAPSHOT:-}" ]; then cp "$FIX/openapi-base.json" "$R/frontend/lib/api/openapi.json"; fi
  git -C "$R" add -A
  git -C "$R" commit -q -m base --allow-empty
  git -C "$R" checkout -q -b pr
  cp "$1" "$R/frontend/lib/api/openapi.json"
  git -C "$R" add -A
  git -C "$R" commit -q -m change --allow-empty
  git -C "$R" checkout -q base
  echo other > "$R/other.txt"          # the base moves on, so the merge is a real merge commit
  git -C "$R" add other.txt
  git -C "$R" commit -q -m "base moves"
  git -C "$R" merge -q --no-ff -m merge pr
}

@test "pull request: a breaking change against the first parent fails, and passes with the label" {
  merge_with "$FIX/openapi-breaking.json"
  PATH="$ROOT/.tools/bin:$PATH" run bash "$R/scripts/ci/oasdiff.sh"
  [ "$status" -eq 1 ]
  PATH="$ROOT/.tools/bin:$PATH" BREAKING_CHANGE_LABEL=true run bash "$R/scripts/ci/oasdiff.sh"
  [ "$status" -eq 0 ]
}

@test "pull request: a compatible change passes" {
  merge_with "$FIX/openapi-compatible.json"
  PATH="$ROOT/.tools/bin:$PATH" run bash "$R/scripts/ci/oasdiff.sh"
  [ "$status" -eq 0 ]
}

@test "pull request: a base that moved on is not read as a removal (the base is the first parent)" {
  merge_with "$FIX/openapi-base.json"   # the pull request changes nothing in the snapshot
  PATH="$ROOT/.tools/bin:$PATH" run bash "$R/scripts/ci/oasdiff.sh"
  [ "$status" -eq 0 ]
}

@test "pull request: HEAD that is not a merge commit fails with a clear message" {
  merge_with "$FIX/openapi-base.json"
  git -C "$R" checkout -q --detach HEAD^1
  PATH="$ROOT/.tools/bin:$PATH" run bash "$R/scripts/ci/oasdiff.sh"
  [ "$status" -eq 2 ]
  [[ "$output" == *"not a merge commit"* ]]
}

@test "pull request: no base snapshot yet skips" {
  NO_BASE_SNAPSHOT=1 merge_with "$FIX/openapi-base.json"
  PATH="$ROOT/.tools/bin:$PATH" run bash "$R/scripts/ci/oasdiff.sh"
  [ "$status" -eq 0 ]
  [[ "$output" == *"has no frontend/lib/api/openapi.json yet"* ]]
}
