#!/usr/bin/env bash
# your-service-name — adaptable service template (see wiki/getting-started.md)
# SAMPLE/REFERENCE: Run Alembic migrations before starting the backend.
set -euo pipefail

echo "[migrations] Running Alembic migrations..."
python -m your_service_name.backend.db.run_migrations || {
    echo "[migrations] FATAL: Migration failed. Exiting." >&2
    exit 1
}

# Hand off to the standard backend entrypoint
exec /app/infra/runtime/entrypoint.sh --mode backend "$@"
