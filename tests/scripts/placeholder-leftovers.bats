#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# placeholder-leftovers.bats — scripts/ci/check-placeholder-leftovers.sh in throwaway git repos.
# The placeholders are spelled in two pieces so the adapt tooling never rewrites them.
# -----------------------------------------------------------------------------

OLD="your_""service_name"
DASH="your-""service-name"

setup() {
  SCRIPT="$(dirname "$BATS_TEST_DIRNAME")/../scripts/ci/check-placeholder-leftovers.sh"
  R="$BATS_TEST_TMPDIR/repo"
  mkdir -p "$R"
  git -C "$R" init -q
}

commit_all() { git -C "$R" add -A; }

@test "template repo (placeholder package still tracked): passes even with placeholder text" {
  mkdir -p "$R/src/$OLD"
  echo "x = 1" > "$R/src/$OLD/a.py"
  echo "name: $OLD" > "$R/README.md"
  commit_all
  cd "$R"
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
}

@test "adopted repo with no placeholder: passes" {
  mkdir -p "$R/src/acme"
  echo "x = 1" > "$R/src/acme/a.py"
  echo "name: acme" > "$R/README.md"
  commit_all
  cd "$R"
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
}

@test "adopted repo: a leftover in text fails and names the file" {
  mkdir -p "$R/src/acme"
  echo "x = 1" > "$R/src/acme/a.py"
  echo "deploy $DASH now" > "$R/docs.md"
  commit_all
  cd "$R"
  run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"docs.md:1"* ]]
}

@test "adopted repo: a leftover in a path fails" {
  mkdir -p "$R/src/acme" "$R/charts/$DASH"
  echo "x = 1" > "$R/src/acme/a.py"
  echo "a: b" > "$R/charts/$DASH/values.yaml"
  commit_all
  cd "$R"
  run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"path: charts/$DASH/values.yaml"* ]]
}

@test "adopted repo: the adapt tooling, its test and the changelog may keep the placeholder" {
  mkdir -p "$R/src/acme" "$R/infra/adapt" "$R/infra/security/local/tests" "$R/wiki"
  echo "x = 1" > "$R/src/acme/a.py"
  echo "OLD=$OLD" > "$R/infra/adapt/adopt.conf"
  echo "# $OLD" > "$R/infra/security/local/tests/adapt.bats"
  echo "renamed from $OLD" > "$R/wiki/changelog.md"
  commit_all
  cd "$R"
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
}
