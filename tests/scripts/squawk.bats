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

# The exception for prefer-text-field covers revision 0001 only: a later revision is linted with the defaults.
@test "a new revision adding a VARCHAR column is flagged" {
  [ -x "$ROOT/.venv/bin/python" ] || skip "needs the backend virtualenv (alembic)"
  scripts="$BATS_TEST_TMPDIR/migrations"
  cp -r "$ROOT/src/your_service_name/backend/db/migrations" "$scripts"
  cat > "$scripts/versions/0002_add_tag.py" <<'PY'
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("items", sa.Column("tag", sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column("items", "tag")
PY
  SQUAWK_SCRIPT_LOCATION="$scripts" run bash "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"prefer-text-field"* ]]
}
