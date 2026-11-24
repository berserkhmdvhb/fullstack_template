#!/usr/bin/env bash
# adopted-copy.sh DEST — copy this working tree to DEST (an empty or missing directory) and adopt it the way an
# adopter does: infra/adapt/adopt.sh with a test identity and every optional value set. The copy is a git
# repository with two commits, the template and the adoption, so a test can diff the two.
#
# Run from the repo root. Used by tests/scripts/adopt-e2e.bats and the CI job that runs the copy's own suites.
set -euo pipefail

dest="${1:?usage: scripts/ci/adopted-copy.sh DEST}"
[[ ! -e "$dest" || -z "$(ls -A "$dest")" ]] || { echo "adopted-copy: $dest is not empty" >&2; exit 2; }
mkdir -p "$dest"
dest="$(cd "$dest" && pwd)"

# The working tree as a fresh clone would see it: tracked plus untracked-not-ignored files that exist.
git ls-files -z --cached --others --exclude-standard \
  | while IFS= read -r -d '' f; do [[ -f "$f" ]] && printf '%s\0' "$f"; done \
  | xargs -0 cp --parents -P -p -t "$dest"

# --allow-empty: adopting a copy that is already adopted changes nothing.
commit() {
  git -C "$dest" -c user.name=adopted-copy -c user.email=adopted-copy@example.test commit -q --allow-empty -m "$1"
}
git -C "$dest" init -q -b main
git -C "$dest" add -A
commit template

# The pinned tools, when installed here: the adoption renders the chart's snapshots with them. A link is not
# a directory, so .gitignore's /.tools/ misses it; the copy's own exclude file keeps it out.
if [[ -d .tools ]]; then
  ln -s "$PWD/.tools" "$dest/.tools"
  echo /.tools >> "$dest/.git/info/exclude"
fi
# The config stays in the copy's .adapt-backup/ (ignored), so a test can run the adoption again.
conf="$dest/.adapt-backup/adopt.conf"
mkdir -p "$dest/.adapt-backup"
printf '%s\n' 'SERVICE_NAME="acme_orders"' 'DOMAIN="acme.example.test"' 'REGISTRY="ghcr.io/acme/orders"' \
  'REPOSITORY="acme/orders"' 'CODEOWNER="@acme/core"' > "$conf"
# This tree's ruff formats the copy, which has no backend installed yet.
[[ ! -x .venv/bin/ruff ]] || export RUFF="$PWD/.venv/bin/ruff"
(cd "$dest" && ADOPT_CONF="$conf" ADAPT_ROOT="$dest" bash infra/adapt/adopt.sh)
git -C "$dest" add -A
commit adopted
echo "adopted-copy: $dest"
