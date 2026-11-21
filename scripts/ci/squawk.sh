#!/usr/bin/env bash
# squawk.sh — lint the migrations' PostgreSQL SQL with the pinned squawk (blocking; backs the backend lint job).
#
#   scripts/ci/squawk.sh            render `alembic upgrade --sql` for PostgreSQL (offline: no database), base to
#                                   0001 and 0001 to head, and lint both
#   scripts/ci/squawk.sh FILE...    lint these SQL files instead (the tests use this)
#
# The rules are squawk's defaults (.squawk.toml sets the PostgreSQL version); the one exception is scoped below. Install the pinned
# binary with `scripts/tools.sh install squawk`.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PATH="$root/.venv/bin:$root/.tools/bin:$PATH"
command -v squawk >/dev/null || { echo "squawk: not found; run scripts/tools.sh install squawk" >&2; exit 2; }

if (( $# > 0 )); then
  exec squawk --config "$root/.squawk.toml" "$@"
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cd "$root"
# The two ranges are rendered offline: only the dialect matters, the PostgreSQL URL has no host and is never
# connected to. The alembic output buffer keeps Alembic's log lines out of the SQL. SQUAWK_SCRIPT_LOCATION
# points at another script directory (the tests add a revision to a copy).
python - "$tmp" <<'PY'
import io
import os
import sys

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

LEGACY = "0001"  # the revision with the documented exception (see .squawk.toml)

cfg = Config("alembic.ini")
cfg.set_main_option("sqlalchemy.url", "postgresql+asyncpg://")
if os.environ.get("SQUAWK_SCRIPT_LOCATION"):
    cfg.set_main_option("script_location", os.environ["SQUAWK_SCRIPT_LOCATION"])
head = ScriptDirectory.from_config(cfg).get_current_head()
ranges = [("legacy", f"base:{LEGACY}")]
if head != LEGACY:
    ranges.append(("current", f"{LEGACY}:head"))
for name, rng in ranges:
    cfg.output_buffer = io.StringIO()
    command.upgrade(cfg, rng, sql=True)
    with open(os.path.join(sys.argv[1], f"{name}.sql"), "w", encoding="utf-8") as out:
        out.write(cfg.output_buffer.getvalue())
PY
echo "squawk: linting the offline PostgreSQL SQL (base to head)"
# prefer-text-field is excluded for revision 0001 only (.squawk.toml says why); every later revision gets the defaults.
squawk --config "$root/.squawk.toml" --exclude prefer-text-field "$tmp/legacy.sql"
if [[ -f "$tmp/current.sql" ]]; then
  squawk --config "$root/.squawk.toml" "$tmp/current.sql"
fi
