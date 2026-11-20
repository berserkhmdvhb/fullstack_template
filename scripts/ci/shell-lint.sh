#!/usr/bin/env bash
# shell-lint.sh — repo-wide shell style gate (backs the `shellcheck` job in
# .github/workflows/ci.yml).
#
# Lints every git-tracked shell script at shellcheck's DEFAULT level
# (error + warning + info + style — the gate is green only when ALL scripts are
# clean): plain *.sh in their default dialect, and *.bats parsed as bash (bats
# files are bash). shellcheck exits non-zero on the first finding, and that
# non-zero IS the gate verdict — so it runs last and its status propagates as
# the job's. There is deliberately no wrapper and no `|| die`: this script is the
# gate itself, not a report-writer around one.
#
# Run from CI as `bash scripts/ci/shell-lint.sh`: the script carries
# `#!/usr/bin/env bash` and is executed under bash explicitly, because it uses
# `mapfile` (bash-only). A script file run as `bash …` also makes the gate
# itself lintable (it is a tracked *.sh) and testable.
#
# Usage: shell-lint.sh [SHELLCHECK]
set -euo pipefail

SHELLCHECK_BIN="${1:-shellcheck}"

# Lint every tracked shell script in a stable order. *.bats are bats/bash, so
# they are parsed with `-s bash`; plain scripts use the default dialect.
mapfile -t sh_files   < <(git ls-files '*.sh'   | LC_ALL=C sort)
mapfile -t bats_files < <(git ls-files '*.bats' | LC_ALL=C sort)

if (( ${#sh_files[@]} + ${#bats_files[@]} == 0 )); then
  echo "no shell scripts tracked by git" >&2
  exit 1
fi

echo "shellchecking ${#sh_files[@]} *.sh and ${#bats_files[@]} *.bats (default level; .bats as bash)"

if (( ${#sh_files[@]} > 0 )); then
  "${SHELLCHECK_BIN}" "${sh_files[@]}"
fi
if (( ${#bats_files[@]} > 0 )); then
  "${SHELLCHECK_BIN}" -s bash "${bats_files[@]}"
fi
