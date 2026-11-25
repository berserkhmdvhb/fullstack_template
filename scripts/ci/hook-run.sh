#!/usr/bin/env bash
# hook-run.sh TOOL CMD... — run a git hook command with the repo's own tools first on PATH.
#
# Every hook calls the tool the CI jobs use (the project venv, or .tools from mise.toml and tools/pins.tsv), so a
# version lives in one place. When TOOL is missing this says how to get it instead of a bare
# "command not found".
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
PATH="$PWD/.venv/bin:$PWD/.tools/bin:$PATH"
export PATH

tool="${1:?usage: hook-run.sh TOOL CMD...}"
shift
if ! command -v "$tool" >/dev/null 2>&1; then
  case "$tool" in
    ruff) hint="make develop" ;;
    npm) hint="scripts/tools.sh install node, then npm --prefix frontend ci" ;;
    *) hint="scripts/tools.sh install $tool" ;;
  esac
  echo "hook: $tool not found; run: $hint" >&2
  exit 1
fi
exec "$@"
