#!/usr/bin/env bash
# check-placeholder-leftovers.sh — fail an ADOPTED repo that still mentions the template placeholder.
#
# Template mode: src/<placeholder>/ still exists on disk, so the placeholder is meant to be there; pass.
# Adopted mode: the package was renamed, so any path or text in the working tree that still names the
# placeholder (underscore or hyphen form) is a missed rename; list it and fail.
# Allowed to keep the placeholder: the paths in infra/adapt/excludes.txt (what the adapter leaves alone).
#
# Run from the repo root (prek does). The placeholders are spelled in two pieces so the
# adapt tooling, which rewrites this very file, can never turn them into the new name.
set -euo pipefail

under="your_""service_name"
dash="your-""service-name"

# Decided from the working tree, not the index: a rename that is not staged yet must still be checked.
if [[ -n "$(find "src/${under}" -type f -not -path '*/__pycache__/*' -print -quit 2>/dev/null)" ]]; then
  echo "placeholder-leftovers: template repo (src/${under} exists); nothing to check"
  exit 0
fi

# The paths the adapter never rewrites: the one list infra/adapt/excludes.txt, read by the adapter too.
allow=()
while IFS= read -r line; do
  [[ -z "$line" || "$line" == \#* ]] || allow+=(":(exclude)$line")
done < infra/adapt/excludes.txt

# Working-tree files: tracked and untracked-not-ignored; a path still in the index but gone from disk is skipped.
hits="$(git grep --untracked -nIF -e "$under" -e "$dash" -- . "${allow[@]}" 2>/dev/null || true)"
paths="$(git ls-files --cached --others --exclude-standard -- . "${allow[@]}" \
  | while IFS= read -r f; do [[ -e "$f" ]] && printf '%s\n' "$f"; done \
  | grep -F -e "$under" -e "$dash" || true)"

if [[ -n "$hits$paths" ]]; then
  echo "placeholder-leftovers: the placeholder survives in an adopted repo:" >&2
  [[ -z "$paths" ]] || printf 'path: %s\n' "$paths" >&2
  [[ -z "$hits" ]] || printf '%s\n' "$hits" >&2
  exit 1
fi
echo "placeholder-leftovers: none"
