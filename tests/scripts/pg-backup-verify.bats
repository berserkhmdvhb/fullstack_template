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
  *"FROM pg_database"*)
    # The server's databases (DATABASES, one per line), without the connected one when asked.
    # BUSY_DBS have a session, left out when the query excludes pg_stat_activity's databases.
    for d in ${DATABASES:-}; do
      [[ "$*" == *"current_database()"* && "$d" == "${DB_NAME}" ]] && continue
      [[ "$*" == *"pg_stat_activity"* && " ${BUSY_DBS:-} " == *" $d "* ]] && continue
      echo "$d"
    done; exit 0 ;;
  *"CREATE DATABASE"*) exit 0 ;;
  *"DROP DATABASE"*) [ -z "${DROP_FAIL:-}" ] || { echo 'ERROR:  database is being accessed by other users' >&2; exit 1; }; exit 0 ;;
  *"FROM alembic_version"*) echo "${ALEMBIC_ROWS:-1}"; exit 0 ;;
  *"table_name = '"*)
    all="$*" q="'"; t="${all##*table_name = $q}"; t="${t%%"$q"*}"
    if [ "$t" = "${MISSING_TABLE:-}" ]; then echo 0; else echo 1; fi; exit 0 ;;
  *"table_schema = 'public';"*)
    [ -z "${QUERY_FAIL:-}" ] || { echo 'psql: error: server closed the connection unexpectedly' >&2; exit 2; }
    echo "${PUBLIC_TABLES:-3}"; exit 0 ;;
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
  export VERIFY_TABLES=alembic_version,items,echo_records VERIFY_MAX_AGE_HOURS=26 VERIFY_STALE_AFTER_SECONDS=86400
  dump "$BACKUP_DIR/svc-old.sql.gz"
  touch -d '-3 days' "$BACKUP_DIR/svc-old.sql.gz"
  dump "$BACKUP_DIR/svc-new.sql.gz"
}

# A dump as pg_dump ends it: the trailer marks a complete one (16.10 and later add \unrestrict after it).
dump() { printf 'SELECT 1;\n--\n-- PostgreSQL database dump complete\n--\n\n\\unrestrict k3y\n\n' | gzip > "$1"; }

# The temporary database this run created was dropped.
dropped() {
  name="$(sed -n 's/.*CREATE DATABASE "\(verify_backup_[0-9a-f_]*\)";.*/\1/p' "$LOG")"
  [ -n "$name" ] && grep -q "DROP DATABASE IF EXISTS \"$name\";" "$LOG"
}

created_none() { [ "$(grep -c 'CREATE DATABASE' "$LOG")" -eq 0 ]; }

@test "a good restore passes, restores the newest dump and drops the temporary database" {
  run sh "$SCRIPT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Latest backup: $BACKUP_DIR/svc-new.sql.gz"* ]]
  [[ "$output" == *"Backup verification completed successfully."* ]]
  for t in alembic_version items echo_records; do grep -q "table_name = '$t'" "$LOG"; done
  dropped
}

@test "no dump fails, naming it" {
  rm "$BACKUP_DIR"/*.sql.gz
  run sh "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"no dump"* ]]
  created_none
}

@test "an unfinished dump (.partial) is never the one restored" {
  dump "$BACKUP_DIR/svc-newer.sql.gz.partial"
  run sh "$SCRIPT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Latest backup: $BACKUP_DIR/svc-new.sql.gz"* ]]
}

@test "a dump older than the maximum age fails, naming its age, and creates no database" {
  touch -d '-30 hours' "$BACKUP_DIR/svc-new.sql.gz"
  run sh "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"older than 26 hours"* ]]
  created_none
}

@test "VERIFY_MAX_AGE_HOURS sets the maximum age" {
  touch -d '-30 hours' "$BACKUP_DIR/svc-new.sql.gz"
  VERIFY_MAX_AGE_HOURS=48 run sh "$SCRIPT"
  [ "$status" -eq 0 ]
}

@test "without VERIFY_MAX_AGE_HOURS or VERIFY_TABLES the script refuses to run (the chart's values are the defaults)" {
  run env -u VERIFY_MAX_AGE_HOURS sh "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"VERIFY_MAX_AGE_HOURS"* ]]
  run env -u VERIFY_TABLES sh "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"VERIFY_TABLES"* ]]
  created_none
}

@test "an empty VERIFY_TABLES is refused" {
  VERIFY_TABLES="" run sh "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"VERIFY_TABLES"* ]]
  created_none
}

@test "a restore error fails and drops the temporary database" {
  RESTORE_FAIL=1 run sh "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"restore failed"* ]]
  dropped
}

@test "a corrupt dump fails before any database is created" {
  echo 'not gzip' > "$BACKUP_DIR/svc-new.sql.gz"
  run sh "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"not a readable gzip file"* ]]
  created_none
}

@test "a dump without pg_dump's trailer (cut short) fails before any database is created" {
  printf 'SELECT 1;\n' | gzip > "$BACKUP_DIR/svc-new.sql.gz"
  run sh "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"incomplete"* ]]
  created_none
}

@test "a failed query fails, naming it, and drops the temporary database" {
  QUERY_FAIL=1 run sh "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"query failed"* ]]
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

@test "a table name that is not a lower-case identifier is refused before any database is created" {
  for bad in "items;drop" "Items" "public.items"; do
    VERIFY_TABLES="alembic_version,$bad" run sh "$SCRIPT"
    [ "$status" -ne 0 ]
    [[ "$output" == *"not a lower-case table name"* ]]
  done
  created_none
}

@test "an empty alembic_version fails and drops the temporary database" {
  ALEMBIC_ROWS=0 run sh "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"alembic_version holds no row"* ]]
  dropped
}

@test "a failed drop of the temporary database fails the run, naming it" {
  DROP_FAIL=1 run sh "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"could not drop the temporary database verify_backup_"* ]]
}

@test "the temporary database's name is unique per run: the second, then a random suffix" {
  run sh "$SCRIPT"
  [ "$status" -eq 0 ]
  grep -Eq 'CREATE DATABASE "verify_backup_[0-9]{14}_[0-9a-f]{8}";' "$LOG"
}

@test "a temporary database left by an earlier run is dropped first" {
  DATABASES="app postgres verify_backup_19990101000000_0a1b2c3d" run sh "$SCRIPT"
  [ "$status" -eq 0 ]
  grep -q 'DROP DATABASE IF EXISTS "verify_backup_19990101000000_0a1b2c3d";' "$LOG"
  [ "$(grep -n 'verify_backup_19990101000000_0a1b2c3d' "$LOG" | head -n1 | cut -d: -f1)" -lt "$(grep -n 'CREATE DATABASE' "$LOG" | cut -d: -f1)" ]
}

@test "a temporary database under a day old (another run in progress) is left alone" {
  running="verify_backup_$(date -u -d "@$(($(date +%s) - 7200))" +%Y%m%d%H%M%S)_0a1b2c3d"
  DATABASES="$running" run sh "$SCRIPT"
  [ "$status" -eq 0 ]
  [ "$(grep -c "DROP DATABASE IF EXISTS \"$running\"" "$LOG")" -eq 0 ]
}

@test "a temporary database with a live session is never dropped, however old" {
  DATABASES="verify_backup_19990101000000_0a1b2c3d" BUSY_DBS="verify_backup_19990101000000_0a1b2c3d" run sh "$SCRIPT"
  [ "$status" -eq 0 ]
  [ "$(grep -c 'DROP DATABASE IF EXISTS "verify_backup_19990101000000_0a1b2c3d"' "$LOG")" -eq 0 ]
}

@test "VERIFY_STALE_AFTER_SECONDS sets the age after which a temporary database counts as left over" {
  recent="verify_backup_$(date -u -d "@$(($(date +%s) - 7200))" +%Y%m%d%H%M%S)_0a1b2c3d"
  VERIFY_STALE_AFTER_SECONDS=3600 DATABASES="$recent" run sh "$SCRIPT"
  [ "$status" -eq 0 ]
  grep -q "DROP DATABASE IF EXISTS \"$recent\";" "$LOG"
}

@test "without VERIFY_STALE_AFTER_SECONDS the script refuses to run" {
  run env -u VERIFY_STALE_AFTER_SECONDS sh "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"VERIFY_STALE_AFTER_SECONDS"* ]]
  created_none
}

@test "a date that cannot compute the cut-off fails with a message" {
  cat > "$B/date" <<'STUB'
#!/usr/bin/env bash
for a in "$@"; do [[ "$a" == @* ]] && { echo "date: invalid date '$a'" >&2; exit 1; }; done
exec /usr/bin/env -u PATH /bin/date "$@"
STUB
  chmod +x "$B/date"
  run sh "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"ERROR: could not compute"* ]]
  created_none
}

@test "a temporary database that cannot be dropped is reported and the check goes on" {
  DROP_FAIL=1 DATABASES="verify_backup_19990101000000_0a1b2c3d" run sh "$SCRIPT"
  [[ "$output" == *"could not drop verify_backup_19990101000000_0a1b2c3d"* ]]
  grep -q 'CREATE DATABASE' "$LOG"
}

@test "a database that only starts like a temporary one is never dropped" {
  DATABASES="verify_backup_app verify_backup_19990101000000 verify_backup_19990101000000_0a1b2c3d_x" run sh "$SCRIPT"
  [ "$status" -eq 0 ]
  [ "$(grep -c 'DROP DATABASE' "$LOG")" -eq 1 ]
  dropped
}

@test "the connected database is never dropped, even with a temporary database's name" {
  DB_NAME=verify_backup_19990101000000_0a1b2c3d DATABASES="verify_backup_19990101000000_0a1b2c3d" run sh "$SCRIPT"
  [ "$status" -eq 0 ]
  [ "$(grep -c 'DROP DATABASE' "$LOG")" -eq 1 ]
  dropped
}
