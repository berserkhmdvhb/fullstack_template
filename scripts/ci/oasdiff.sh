#!/usr/bin/env bash
# oasdiff.sh — fail a pull request that makes a breaking change to the committed OpenAPI snapshot, unless it
# carries the `breaking-change` label (backs the `api-compat` job in .github/workflows/ci.yml).
#
#   scripts/ci/oasdiff.sh                  compare the snapshot with the base branch's (the pull request case)
#   scripts/ci/oasdiff.sh BASE.json REV.json   compare two files (the tests use this)
#
# Environment:
#   EVENT_NAME              anything but `pull_request` skips with a summary line (push to main, tags).
#   BASE_REF                the base branch (github.base_ref); fetched shallowly from origin, then read with git show.
#   BASE_REV                a revision already in the repository, used instead of fetching BASE_REF (local runs).
#   BREAKING_CHANGE_LABEL   `true` when the pull request carries the `breaking-change` label.
#   OPENAPI_SNAPSHOT        the snapshot path (default frontend/lib/api/openapi.json, written by `make openapi-gen`).
#
# `oasdiff breaking BASE REVISION --fail-on ERR` exits 1 on any error-level breaking change; see
# https://github.com/oasdiff/oasdiff/blob/main/docs/BREAKING-CHANGES.md and `oasdiff breaking --help`.
# Install the pinned binary with `scripts/tools.sh install oasdiff`.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PATH="$root/.tools/bin:$PATH"
snapshot="${OPENAPI_SNAPSHOT:-frontend/lib/api/openapi.json}"
summary() {
  echo "oasdiff: $*"
  if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then echo "oasdiff: $*" >> "$GITHUB_STEP_SUMMARY"; fi
}

if (( $# == 2 )); then
  base="$1"
  revision="$2"
else
  if [[ "${EVENT_NAME:-pull_request}" != "pull_request" ]]; then
    summary "skipped: the API compatibility check runs on pull requests only (event: ${EVENT_NAME})"
    exit 0
  fi
  cd "$root"
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  base="$tmp/base-openapi.json"
  rev="${BASE_REV:-}"
  if [[ -z "$rev" ]]; then
    : "${BASE_REF:?BASE_REF (the base branch) or BASE_REV is required}"
    git fetch --no-tags --depth=1 origin "$BASE_REF"
    rev=FETCH_HEAD
  fi
  if ! git cat-file -e "$rev:$snapshot" 2>/dev/null; then
    summary "skipped: the base revision has no $snapshot yet"
    exit 0
  fi
  git show "$rev:$snapshot" > "$base"
  revision="$snapshot"
fi

command -v oasdiff >/dev/null || { echo "oasdiff: not found; run scripts/tools.sh install oasdiff" >&2; exit 2; }

if oasdiff breaking "$base" "$revision" --fail-on ERR; then
  summary "no breaking change"
  exit 0
fi
if [[ "${BREAKING_CHANGE_LABEL:-false}" == "true" ]]; then
  summary "breaking changes allowed: the pull request carries the breaking-change label"
  exit 0
fi
summary "breaking changes found. Make them backward compatible, or add the breaking-change label (a maintainer with triage permission or above) once the change is deliberate (see wiki/api-lifecycle.md)"
exit 1
