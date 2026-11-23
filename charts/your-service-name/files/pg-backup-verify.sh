#!/bin/sh
# -----------------------------------------------------------------------------
# pg-backup-verify.sh — the weekly restore check of the in-cluster database, run by the
# <fullname>-pg-backup-verify CronJob (mounted from a ConfigMap, run by postgresql.clientImage).
# Restores the newest dump into a temporary database and fails, naming the reason, when there is
# no dump, the dump is too old, the restore fails, public holds no table, a listed table is missing
# or alembic_version holds no row. The temporary database is dropped on every exit.
# Env: DB_HOST, DB_USER, DB_NAME, PGPASSWORD, PGPORT (the CronJob's); BACKUP_DIR (/backup);
# VERIFY_MAX_AGE_HOURS (26); VERIFY_TABLES (comma-separated: alembic_version,items,echo_records).
# A failure fires BackupVerifyFailed: wiki/operations-runbook.md#backupverifyfailed.
# -----------------------------------------------------------------------------
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backup}"
MAX_AGE_HOURS="${VERIFY_MAX_AGE_HOURS:-26}"
TABLES="${VERIFY_TABLES:-alembic_version,items,echo_records}"
TEMP_DB=""

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

psql_on() {
  db="$1"
  shift
  psql -v ON_ERROR_STOP=1 -X -q -t -A -h "$DB_HOST" -U "$DB_USER" -d "$db" "$@"
}

cleanup() {
  rc=$?
  if [ -n "$TEMP_DB" ]; then
    if ! psql_on "$DB_NAME" -c "DROP DATABASE IF EXISTS ${TEMP_DB};"; then
      echo "ERROR: could not drop the temporary database ${TEMP_DB}" >&2
      [ "$rc" -ne 0 ] || rc=1
    fi
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 1' INT TERM

TABLE_LIST=$(echo "$TABLES" | tr ',' ' ')
for t in $TABLE_LIST; do
  case "$t" in
    '' | [0-9]* | *[!A-Za-z0-9_]*) fail "verifyTables: '$t' is not a plain table name" ;;
  esac
done

echo "Starting backup verification ..."
# shellcheck disable=SC2012 # the dump names are the chart's own (<name>-<timestamp>.sql.gz)
LATEST_BACKUP=$(ls -t "$BACKUP_DIR"/*.sql.gz 2>/dev/null | head -n1 || true)
[ -n "$LATEST_BACKUP" ] || fail "no dump (*.sql.gz) in $BACKUP_DIR: see BackupJobFailed and BackupTooOld in the runbook"
echo "Latest backup: $LATEST_BACKUP"

AGE_HOURS=$((($(date +%s) - $(date -r "$LATEST_BACKUP" +%s)) / 3600))
if [ "$AGE_HOURS" -ge "$MAX_AGE_HOURS" ]; then
  fail "the newest dump is ${AGE_HOURS} hours old, older than ${MAX_AGE_HOURS} hours (verifyMaxAgeHours): the nightly backup is not running; see BackupTooOld in the runbook"
fi

TEMP_DB="verify_backup_$(date +%Y%m%d%H%M%S)"
psql_on "$DB_NAME" -c "CREATE DATABASE ${TEMP_DB};"

gunzip -t "$LATEST_BACKUP" 2>/dev/null || fail "restore failed: $LATEST_BACKUP is not a readable gzip file"
gunzip -c "$LATEST_BACKUP" | psql_on "$TEMP_DB" >/dev/null || fail "restore failed: psql stopped on an error restoring $LATEST_BACKUP"

PUBLIC_TABLES=$(psql_on "$TEMP_DB" -c "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';")
[ "$PUBLIC_TABLES" -gt 0 ] || fail "the restored database has no table in schema public"
echo "Tables in public: $PUBLIC_TABLES"

for t in $TABLE_LIST; do
  found=$(psql_on "$TEMP_DB" -c "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name = '$t';")
  [ "$found" -eq 1 ] || fail "table $t is missing from the restored database (verifyTables)"
done

ROWS=$(psql_on "$TEMP_DB" -c "SELECT count(*) FROM alembic_version;")
[ "$ROWS" -gt 0 ] || fail "alembic_version holds no row: the dump has the schema but no migration revision"

echo "Backup verification completed successfully."
