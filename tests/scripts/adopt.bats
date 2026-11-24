#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# adopt.bats — infra/adapt/adopt.sh records the template release in .template-version.
# The placeholder is spelled in two pieces so the adapt tooling never rewrites it.
# -----------------------------------------------------------------------------

OLD="your_""service_name"

setup() {
  REPO="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
  R="$BATS_TEST_TMPDIR/repo"
  mkdir -p "$R"
  cp "$REPO/pyproject.toml" "$R/"
  printf '{\n  ".": "4.2.0"\n}\n' > "$R/.release-please-manifest.json"
  export ADOPT_CONF="$BATS_TEST_TMPDIR/adopt.conf" ADAPT_ROOT="$R"
  printf 'SERVICE_NAME="%s"\nDOMAIN="example.test"\nREGISTRY="registry.example.test"\n' "$OLD" > "$ADOPT_CONF"
}

@test "adoption writes the release tag from release-please's manifest" {
  run bash "$REPO/infra/adapt/adopt.sh"
  [ "$status" -eq 0 ]
  [ "$(cat "$R/.template-version")" = "v4.2.0" ]
  [[ "$output" == *"Wrote .template-version: v4.2.0"* ]]
}

@test "a dry run names the record and writes nothing" {
  run bash "$REPO/infra/adapt/adopt.sh" --dry-run
  [ "$status" -eq 0 ]
  [[ "$output" == *"Would write .template-version: v4.2.0"* ]]
  [ ! -e "$R/.template-version" ]
}

@test "a re-run keeps the recorded version (the adopter moves it after an upgrade)" {
  echo "v4.0.0" > "$R/.template-version"
  run bash "$REPO/infra/adapt/adopt.sh"
  [ "$status" -eq 0 ]
  [ "$(cat "$R/.template-version")" = "v4.0.0" ]
  [[ "$output" == *"already records v4.0.0"* ]]
}

@test "without a manifest version nothing is written and the run still passes" {
  rm "$R/.release-please-manifest.json"
  run bash "$REPO/infra/adapt/adopt.sh"
  [ "$status" -eq 0 ]
  [ ! -e "$R/.template-version" ]
  [[ "$output" == *"is not written"* ]]
}
