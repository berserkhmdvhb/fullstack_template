#!/usr/bin/env bash
# check-placeholder-leftovers.sh — fail an ADOPTED repo that still mentions the template placeholder.
#
# Template mode: src/<placeholder>/ is still tracked, so the placeholder is meant to be there; pass.
# Adopted mode: the package was renamed, so any tracked path or text that still says
# `your_service_name` / `your-service-name` is a missed rename; list it and fail.
# Allowed to keep the placeholder: the adapt tooling and its tests, the changelog, and this gate.
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

allow=(
  ':(exclude)infra/adapt'
  ':(exclude)infra/security/local/tests/adapt.bats'
  ':(exclude)wiki/changelog.md'
  ':(exclude)scripts/ci/check-placeholder-leftovers.sh'
  ':(exclude)tests/scripts/placeholder-leftovers.bats'
)

hits="$(git grep -nIF -e "$under" -e "$dash" -- . "${allow[@]}" || true)"
paths="$(git ls-files -- . "${allow[@]}" | grep -F -e "$under" -e "$dash" || true)"

if [[ -n "$hits$paths" ]]; then
  echo "placeholder-leftovers: the placeholder survives in an adopted repo:" >&2
  [[ -z "$paths" ]] || printf 'path: %s\n' "$paths" >&2
  [[ -z "$hits" ]] || printf '%s\n' "$hits" >&2
  exit 1
fi
echo "placeholder-leftovers: none"
