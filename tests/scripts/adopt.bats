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

@test "running adoption twice keeps the first record" {
  run bash "$REPO/infra/adapt/adopt.sh"
  [ "$status" -eq 0 ]
  printf '{\n  ".": "9.9.9"\n}\n' > "$R/.release-please-manifest.json"
  run bash "$REPO/infra/adapt/adopt.sh"
  [ "$status" -eq 0 ]
  [ "$(cat "$R/.template-version")" = "v4.2.0" ]
  [[ "$output" == *"already records v4.2.0"* ]]
}

# ---- the chart snapshots and the Python formatting after the rename ----

@test "a failing chart test aborts with helm-unittest's report, saying the rename is applied and where the backup is" {
  mkdir -p "$R/charts/your-""service-name/tests/__snapshot__" "$R/.tools/bin" "$R/.tools/helm-plugins"
  printf '#!/bin/sh\necho "FAIL  the route table\\n  snapshot differs"\nexit 1\n' > "$R/.tools/bin/helm"
  chmod +x "$R/.tools/bin/helm"
  run bash "$REPO/infra/adapt/adopt.sh"
  [ "$status" -eq 1 ]
  [[ "$output" == *"FAIL  the route table"* ]]
  [[ "$output" == *"snapshot differs"* ]]
  [[ "$output" == *"rename and the stamp are already applied"* ]]
  [[ "$output" == *"backups in: $R/.adapt-backup/"* ]]
}

# A stand-in ruff that logs its arguments, and fails when RUFF_FAILS is set.
fake_ruff() {
  mkdir -p "$BATS_TEST_TMPDIR/bin"
  # shellcheck disable=SC2016 # expanded by the stub, not here
  printf '#!/bin/sh\necho "$*" >> "%s"\n[ -z "${RUFF_FAILS:-}" ]\n' "$BATS_TEST_TMPDIR/ruff.log" > "$BATS_TEST_TMPDIR/bin/ruff"
  chmod +x "$BATS_TEST_TMPDIR/bin/ruff"
  cp "$REPO/Makefile" "$R/"
  export RUFF="$BATS_TEST_TMPDIR/bin/ruff"
}

@test "the Python step only sorts the imports and formats, over the Makefile's paths" {
  fake_ruff
  run bash "$REPO/infra/adapt/adopt.sh"
  [ "$status" -eq 0 ]
  paths="$(sed -n 's/^RUFF_PATHS := //p' "$REPO/Makefile")"
  [ -n "$paths" ]
  run cat "$BATS_TEST_TMPDIR/ruff.log"
  [ "${lines[0]}" = "check --fix --select I $paths" ]
  [ "${lines[1]}" = "format $paths" ]
  [ "${#lines[@]}" -eq 2 ]
}

@test "a ruff failure is a warning naming the command, not an abort" {
  fake_ruff
  RUFF_FAILS=1 run bash "$REPO/infra/adapt/adopt.sh"
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARNING: ruff could not sort or format every file"*"make ruff-imports-fix ruff-format"* ]]
  [[ "$output" == *"Adoption complete"* ]]
}
