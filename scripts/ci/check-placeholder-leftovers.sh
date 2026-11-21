#!/usr/bin/env bash
# check-placeholder-leftovers.sh — fail an ADOPTED repo that still mentions the template placeholder.
#
# Template mode: src/<placeholder>/ is still tracked, so the placeholder is meant to be there; pass.
# Adopted mode: the package was renamed, so any tracked path or text that still says
# placeholder name (underscore or hyphen form) is a missed rename; list it and fail.
# Allowed to keep the placeholder: the paths in infra/adapt/excludes.txt (what the adapter leaves alone).
#
# Run from the repo root (pre-commit does). The placeholders are spelled in two pieces so the
# adapt tooling, which rewrites this very file, can never turn them into the new name.
set -euo pipefail

under="your_""service_name"
dash="your-""service-name"

if [[ -n "$(git ls-files -- "src/${under}")" ]]; then
  echo "placeholder-leftovers: template repo (src/${under} still tracked); nothing to check"
  exit 0
fi

# The paths the adapter never rewrites: the one list infra/adapt/excludes.txt, read by the adapter too.
allow=()
while IFS= read -r line; do
  [[ -z "$line" || "$line" == \#* ]] || allow+=(":(exclude)$line")
done < infra/adapt/excludes.txt

hits="$(git grep -nIF -e "$under" -e "$dash" -- . "${allow[@]}" || true)"
paths="$(git ls-files -- . "${allow[@]}" | grep -F -e "$under" -e "$dash" || true)"

if [[ -n "$hits$paths" ]]; then
  echo "placeholder-leftovers: the placeholder survives in an adopted repo:" >&2
  [[ -z "$paths" ]] || printf 'path: %s\n' "$paths" >&2
  [[ -z "$hits" ]] || printf '%s\n' "$hits" >&2
  exit 1
fi
echo "placeholder-leftovers: none"
