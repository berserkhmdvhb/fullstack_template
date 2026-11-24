#!/usr/bin/env bash
# adopted-copy.sh DEST — copy this working tree to DEST (an empty or missing directory) and adopt it the way an
# adopter does: infra/adapt/adopt.sh with a test identity and every optional value set. The copy is a git
# repository with two commits, the template and the adoption, so a test can diff the two.
#
# Run from the repo root. Used by tests/scripts/adopt-e2e.bats and the CI job that runs the copy's own suites.
set -euo pipefail

dest="${1:?usage: scripts/ci/adopted-copy.sh DEST}"
# The adoption must leave a copy that passes its own lint and chart tests, so it needs the tools that
# sort and format the Python and render the chart's snapshots; without them adopt.sh only prints a note.
ruff="${RUFF:-$PWD/.venv/bin/ruff}"
[[ -x "$ruff" ]] || { echo "adopted-copy: no ruff at $ruff (install this tree's backend: .tools/bin/uv sync --locked)" >&2; exit 2; }
[[ -x .tools/bin/helm && -d .tools/helm-plugins ]] \
  || { echo "adopted-copy: no pinned helm and plugin (scripts/tools.sh install helm4 helm-unittest)" >&2; exit 2; }

[[ ! -e "$dest" || -z "$(ls -A "$dest")" ]] || { echo "adopted-copy: $dest is not empty" >&2; exit 2; }
mkdir -p "$dest"
dest="$(cd "$dest" && pwd)"

# The working tree as a fresh clone would see it: tracked plus untracked-not-ignored files that exist
# (a tracked file deleted here is left out). tar keeps modes and links, and runs on GNU and BSD alike.
git ls-files -z --cached --others --exclude-standard \
  | while IFS= read -r -d '' f; do if [[ -f "$f" || -L "$f" ]]; then printf '%s\0' "$f"; fi; done \
  | tar -cf - --null -T - | tar -xpf - -C "$dest"

# --allow-empty: adopting a copy that is already adopted changes nothing.
commit() {
  git -C "$dest" -c user.name=adopted-copy -c user.email=adopted-copy@example.test commit -q --allow-empty -m "$1"
}
git -C "$dest" init -q -b main
git -C "$dest" add -A
commit template

# The pinned tools: the adoption renders the chart's snapshots with them. A link is not a directory, so
# .gitignore's /.tools/ misses it; the copy's own exclude file keeps it out.
ln -sfn "$PWD/.tools" "$dest/.tools"
echo /.tools >> "$dest/.git/info/exclude"
# The test identity, defined here only. The config stays in the copy's .adapt-backup/ (ignored), so a test
# can read the name from it and run the adoption again.
conf="$dest/.adapt-backup/adopt.conf"
mkdir -p "$dest/.adapt-backup"
printf '%s\n' 'SERVICE_NAME="acme_orders"' 'DOMAIN="acme.example.test"' 'REGISTRY="ghcr.io/acme/orders"' \
  'REPOSITORY="acme/orders"' 'CODEOWNER="@acme/core"' > "$conf"
# This tree's ruff formats the copy, which has no backend installed yet.
(cd "$dest" && RUFF="$ruff" ADOPT_CONF="$conf" ADAPT_ROOT="$dest" bash infra/adapt/adopt.sh)
git -C "$dest" add -A
commit adopted
echo "adopted-copy: $dest"
