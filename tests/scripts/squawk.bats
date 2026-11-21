#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# squawk.bats — scripts/ci/squawk.sh: a violating SQL file fails, a clean one passes, and the real
# migrations (rendered offline for PostgreSQL) pass. Needs the pinned squawk (scripts/tools.sh install squawk).
# -----------------------------------------------------------------------------

setup() {
  ROOT="$(dirname "$BATS_TEST_DIRNAME")/.."
  SCRIPT="$ROOT/scripts/ci/squawk.sh"
  FIX="$BATS_TEST_DIRNAME/fixtures"
}

@test "a blocking-index migration fails" {
  run bash "$SCRIPT" "$FIX/squawk-violation.sql"
  [ "$status" -ne 0 ]
  [[ "$output" == *"require-concurrent-index-creation"* ]]
}

@test "a clean migration passes" {
  run bash "$SCRIPT" "$FIX/squawk-clean.sql"
  [ "$status" -eq 0 ]
}

@test "the repository's migrations pass (no arguments: rendered offline, base to head)" {
  [ -x "$ROOT/.venv/bin/python" ] || skip "needs the backend virtualenv (alembic)"
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
}
