#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# pg-backup-verify.bats — the chart's weekly restore check (charts/<chart>/files/pg-backup-verify.sh)
# against a stub psql that records its arguments. Real gzip dumps in a scratch backup directory; the
# stub's answers come from the environment. No database is contacted.
# -----------------------------------------------------------------------------

setup() {
  SCRIPT="$(dirname "$BATS_TEST_DIRNAME")/../charts/your-service-name/files/pg-backup-verify.sh"
  B="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$B"
  export LOG="$BATS_TEST_TMPDIR/psql.log"
  : > "$LOG"
  cat > "$B/psql" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$LOG"
case "$*" in
  *"CREATE DATABASE"*|*"DROP DATABASE"*) exit 0 ;;
  *"FROM alembic_version"*) echo "${ALEMBIC_ROWS:-1}"; exit 0 ;;
  *"table_name = '"*)
    all="$*" q="'"; t="${all##*table_name = $q}"; t="${t%%"$q"*}"
    if [ "$t" = "${MISSING_TABLE:-}" ]; then echo 0; else echo 1; fi; exit 0 ;;
  *"table_schema = 'public'"*) echo "${PUBLIC_TABLES:-3}"; exit 0 ;;
  *" -c "*) exit 0 ;;
esac
cat > /dev/null
if [ -n "${RESTORE_FAIL:-}" ]; then echo 'ERROR:  syntax error at or near "garbage"' >&2; exit 3; fi
STUB
  chmod +x "$B/psql"
  export PATH="$B:$PATH"
  export BACKUP_DIR="$BATS_TEST_TMPDIR/backup"
  mkdir -p "$BACKUP_DIR"
  export DB_HOST=db DB_USER=app DB_NAME=app
  unset VERIFY_TABLES VERIFY_MAX_AGE_HOURS
  echo 'SELECT 1;' | gzip > "$BACKUP_DIR/svc-old.sql.gz"
  touch -d '-3 days' "$BACKUP_DIR/svc-old.sql.gz"
  echo 'SELECT 1;' | gzip > "$BACKUP_DIR/svc-new.sql.gz"
}

dropped() { grep -q 'DROP DATABASE IF EXISTS verify_backup_' "$LOG"; }

@test "a good restore passes, restores the newest dump and drops the temporary database" {
  run sh "$SCRIPT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Latest backup: $BACKUP_DIR/svc-new.sql.gz"* ]]
  [[ "$output" == *"Backup verification completed successfully."* ]]
  grep -q 'CREATE DATABASE verify_backup_' "$LOG"
  for t in alembic_version items echo_records; do grep -q "table_name = '$t'" "$LOG"; done
  dropped
}

@test "no dump fails, naming it" {
  rm "$BACKUP_DIR"/*.sql.gz
  run sh "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"no dump"* ]]
  [ "$(grep -c 'CREATE DATABASE' "$LOG")" -eq 0 ]
}

@test "a dump older than the maximum age fails, naming its age" {
  touch -d '-30 hours' "$BACKUP_DIR/svc-new.sql.gz"
  run sh "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"older than 26 hours"* ]]
}

@test "VERIFY_MAX_AGE_HOURS sets the maximum age" {
  touch -d '-30 hours' "$BACKUP_DIR/svc-new.sql.gz"
  VERIFY_MAX_AGE_HOURS=48 run sh "$SCRIPT"
  [ "$status" -eq 0 ]
}

@test "a restore error fails and drops the temporary database" {
  RESTORE_FAIL=1 run sh "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"restore failed"* ]]
  dropped
}

@test "a corrupt dump fails and drops the temporary database" {
  echo 'not gzip' > "$BACKUP_DIR/svc-new.sql.gz"
  run sh "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"restore failed"* ]]
  dropped
}

@test "no table in public fails and drops the temporary database" {
  PUBLIC_TABLES=0 run sh "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"no table in schema public"* ]]
  dropped
}

@test "a missing listed table fails, naming it, and drops the temporary database" {
  MISSING_TABLE=echo_records run sh "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"table echo_records is missing"* ]]
  dropped
}

@test "VERIFY_TABLES sets the tables checked" {
  VERIFY_TABLES="alembic_version,orders" MISSING_TABLE=orders run sh "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"table orders is missing"* ]]
  [ "$(grep -c "table_name = 'items'" "$LOG")" -eq 0 ]
}

@test "a table name that is not a plain identifier is refused before any database is created" {
  VERIFY_TABLES="items;drop" run sh "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"not a plain table name"* ]]
  [ "$(grep -c 'CREATE DATABASE' "$LOG")" -eq 0 ]
}

@test "an empty alembic_version fails and drops the temporary database" {
  ALEMBIC_ROWS=0 run sh "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"alembic_version holds no row"* ]]
  dropped
}
