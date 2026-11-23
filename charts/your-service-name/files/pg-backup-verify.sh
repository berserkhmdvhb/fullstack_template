#!/bin/sh
# -----------------------------------------------------------------------------
# pg-backup-verify.sh — the weekly restore check of the in-cluster database, run by the
# <fullname>-pg-backup-verify CronJob (mounted from a ConfigMap, run by postgresql.clientImage).
# Restores the newest dump into a temporary database and fails, naming the reason, when there is
# no dump, the dump is too old or incomplete, the restore fails, public holds no table, a listed
# table is missing or alembic_version holds no row. The temporary database is dropped on every
# exit, and one left by an earlier run (killed before its trap ran) at the start of the next.
# Env: DB_HOST, DB_USER, DB_NAME, PGPASSWORD, PGPORT (the CronJob's); BACKUP_DIR (/backup);
# VERIFY_MAX_AGE_HOURS and VERIFY_TABLES (comma-separated), required: the chart's values hold the
# defaults (postgresql.backup.verifyMaxAgeHours, verifyTables).
# A failure fires BackupVerifyFailed: wiki/operations-runbook.md#backupverifyfailed.
# -----------------------------------------------------------------------------
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backup}"
MAX_AGE_HOURS="${VERIFY_MAX_AGE_HOURS:?VERIFY_MAX_AGE_HOURS is required (postgresql.backup.verifyMaxAgeHours)}"
TABLES="${VERIFY_TABLES:?VERIFY_TABLES is required and must name at least one table (postgresql.backup.verifyTables)}"
TEMP_DB=""

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

# psql_on DATABASE ARGS... (sh has no local variables: _on_db is this function's own).
psql_on() {
  _on_db="$1"
  shift
  psql -v ON_ERROR_STOP=1 -X -q -t -A -h "$DB_HOST" -U "$DB_USER" -d "$_on_db" "$@"
}

cleanup() {
  rc=$?
  if [ -n "$TEMP_DB" ]; then
    if ! psql_on "$DB_NAME" -c "DROP DATABASE IF EXISTS \"${TEMP_DB}\";"; then
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
    '' | [0-9]* | *[!a-z0-9_]*) fail "verifyTables: '$t' is not a lower-case table name (an unqualified table in public)" ;;
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

gunzip -t "$LATEST_BACKUP" 2>/dev/null || fail "$LATEST_BACKUP is not a readable gzip file"
# pg_dump writes this trailer last (newer releases add only an \unrestrict line after it):
# without it the dump was cut short.
gunzip -c "$LATEST_BACKUP" | tail -n 10 | grep -q '^-- PostgreSQL database dump complete' ||
  fail "$LATEST_BACKUP is incomplete: it lacks pg_dump's closing '-- PostgreSQL database dump complete'"

# This script's temporary databases: verify_backup_<UTC second>_<8 random hex digits>, unique even
# for a hand-run check that starts in the same second as the scheduled one.
OWN_NAME='^verify_backup_[0-9]{14}_[0-9a-f]{8}$'

# Temporary databases of earlier runs that were killed before their trap ran: only names of this
# script's own format, never the connected database, and only those over a day old (a younger one
# may belong to a run still going). One that cannot be dropped is reported, not fatal.
DATABASES=$(psql_on "$DB_NAME" -c "SELECT datname FROM pg_database WHERE datname <> current_database();") ||
  fail "query failed: listing earlier temporary databases"
DAY_AGO=$(date -u -d "@$(($(date +%s) - 86400))" +%Y%m%d%H%M%S)
for stale in $(printf '%s\n' "$DATABASES" | grep -E "$OWN_NAME" || true); do
  stamp=${stale#verify_backup_}
  stamp=${stamp%_*}
  [ "$stamp" -lt "$DAY_AGO" ] || continue
  echo "Dropping a temporary database left by an earlier run: $stale"
  psql_on "$DB_NAME" -c "DROP DATABASE IF EXISTS \"${stale}\";" ||
    echo "WARNING: could not drop $stale; drop it by hand once no verify Job is active" >&2
done

TEMP_DB="verify_backup_$(date -u +%Y%m%d%H%M%S)_$(od -An -N4 -tx1 /dev/urandom | tr -d ' \n')"
printf '%s\n' "$TEMP_DB" | grep -Eq "$OWN_NAME" || fail "could not make a temporary database name ($TEMP_DB)"
psql_on "$DB_NAME" -c "CREATE DATABASE \"${TEMP_DB}\";"

gunzip -c "$LATEST_BACKUP" | psql_on "$TEMP_DB" >/dev/null || fail "restore failed: psql stopped on an error restoring $LATEST_BACKUP"

PUBLIC_TABLES=$(psql_on "$TEMP_DB" -c "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';") ||
  fail "query failed: counting the tables in public"
[ "$PUBLIC_TABLES" -gt 0 ] || fail "the restored database has no table in schema public"
echo "Tables in public: $PUBLIC_TABLES"

for t in $TABLE_LIST; do
  found=$(psql_on "$TEMP_DB" -c "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name = '$t';") ||
    fail "query failed: looking up table $t"
  [ "$found" -eq 1 ] || fail "table $t is missing from the restored database (verifyTables)"
done

ROWS=$(psql_on "$TEMP_DB" -c "SELECT count(*) FROM alembic_version;") || fail "query failed: reading alembic_version"
[ "$ROWS" -gt 0 ] || fail "alembic_version holds no row: the dump has the schema but no migration revision"

echo "Backup verification completed successfully."
