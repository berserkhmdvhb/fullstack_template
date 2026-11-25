#!/usr/bin/env bash
# hook-run.sh TOOL CMD... — run a git hook command with the repo's own tools first on PATH.
#
# Every hook calls the tool the CI jobs use (the project venv, or .tools from mise.toml and tools/pins.tsv), so a
# version lives in one place. When TOOL is missing this says how to get it instead of a bare
# "command not found".
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
PATH="$PWD/.venv/bin:$PWD/.tools/bin:$PATH"
# pnpm's state file (its update check) under .tools, never under $HOME (as the Makefile runs it).
XDG_STATE_HOME="$PWD/.tools/pnpm/state"
export PATH XDG_STATE_HOME

tool="${1:?usage: hook-run.sh TOOL CMD...}"
shift
if ! command -v "$tool" >/dev/null 2>&1; then
  case "$tool" in
    ruff) hint="make develop" ;;
    pnpm) hint="scripts/tools.sh install node pnpm, then pnpm --dir frontend install --frozen-lockfile" ;;
    *) hint="scripts/tools.sh install $tool" ;;
  esac
  echo "hook: $tool not found; run: $hint" >&2
  exit 1
fi
exec "$@"
