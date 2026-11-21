#!/usr/bin/env bash
# squawk.sh — lint the migrations' PostgreSQL SQL with the pinned squawk (blocking; backs the backend lint job).
#
#   scripts/ci/squawk.sh            render `alembic upgrade base:head --sql` for PostgreSQL (offline: no database)
#                                   and lint it
#   scripts/ci/squawk.sh FILE...    lint these SQL files instead (the tests use this)
#
# The rules come from .squawk.toml (squawk's defaults plus the documented exceptions). Install the pinned
# binary with `scripts/tools.sh install squawk`.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PATH="$root/.venv/bin:$root/.tools/bin:$PATH"
command -v squawk >/dev/null || { echo "squawk: not found; run scripts/tools.sh install squawk" >&2; exit 2; }

if (( $# > 0 )); then
  exec squawk --config "$root/.squawk.toml" "$@"
fi

sql="$(mktemp --suffix=.sql)"
trap 'rm -f "$sql"' EXIT
cd "$root"
# Only the dialect matters here: the PostgreSQL URL has no host and is never connected to. The alembic
# output buffer keeps Alembic's log lines out of the SQL.
python - "$sql" <<'PY'
import io
import sys

from alembic import command
from alembic.config import Config

cfg = Config("alembic.ini")
cfg.set_main_option("sqlalchemy.url", "postgresql+asyncpg://")
cfg.output_buffer = io.StringIO()
command.upgrade(cfg, "base:head", sql=True)
with open(sys.argv[1], "w", encoding="utf-8") as out:
    out.write(cfg.output_buffer.getvalue())
PY
echo "squawk: linting the offline PostgreSQL SQL (base to head)"
squawk --config "$root/.squawk.toml" "$sql"
