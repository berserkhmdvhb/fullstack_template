#!/usr/bin/env bash
# oasdiff.sh — fail a pull request that makes a breaking change to the committed OpenAPI snapshot, unless it
# carries the `breaking-change` label (backs the `api-compat` job in .github/workflows/ci.yml).
#
#   scripts/ci/oasdiff.sh                  compare the snapshot with the base branch's (the pull request case)
#   scripts/ci/oasdiff.sh BASE.json REV.json   compare two files (the tests use this)
#
# Environment:
#   EVENT_NAME              anything but `pull_request` skips with a summary line (push to main, tags).
#   BREAKING_CHANGE_LABEL   `true` when the pull request carries the `breaking-change` label.
#   OPENAPI_SNAPSHOT        the snapshot path (default frontend/lib/api/openapi.json, written by `make openapi-gen`).
#
# The base snapshot is read from the first parent of HEAD. On a pull request, actions/checkout tests the merge commit
# (refs/pull/N/merge), which GitHub rebuilds on the current base branch, so its first parent is the base the pull
# request will merge into (`github.event.pull_request.base.sha` is not refreshed when the base moves). The job checks
# out with fetch-depth 2 so that parent is present; no network fetch is needed. Locally, run it on a merge commit.
#
# `oasdiff breaking BASE REVISION --fail-on ERR` exits 1 on any error-level breaking change; see
# https://github.com/oasdiff/oasdiff/blob/main/docs/BREAKING-CHANGES.md and `oasdiff breaking --help`. Any other
# non-zero exit (for example a spec that fails to load) is a failure whatever the label says.
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
  if ! git rev-parse --verify --quiet HEAD^2 >/dev/null; then
    echo "oasdiff: HEAD is not a merge commit (expected the pull request's merge commit with fetch-depth 2)" >&2
    exit 2
  fi
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  base="$tmp/base-openapi.json"
  if ! git cat-file -e "HEAD^1:$snapshot" 2>/dev/null; then
    summary "skipped: the base revision has no $snapshot yet"
    exit 0
  fi
  git show "HEAD^1:$snapshot" > "$base"
  revision="$snapshot"
fi

command -v oasdiff >/dev/null || { echo "oasdiff: not found; run scripts/tools.sh install oasdiff" >&2; exit 2; }

status=0
oasdiff breaking "$base" "$revision" --fail-on ERR || status=$?
case "$status" in
  0)
    summary "no breaking change"
    exit 0
    ;;
  1)
    if [[ "${BREAKING_CHANGE_LABEL:-false}" == "true" ]]; then
      summary "breaking changes allowed: the pull request carries the breaking-change label"
      exit 0
    fi
    summary "breaking changes found. Make them backward compatible, or add the breaking-change label (a maintainer with triage permission or above) once the change is deliberate (see wiki/api-lifecycle.md)"
    exit 1
    ;;
  *)
    summary "oasdiff failed (exit $status), for example on a spec that does not load; the label does not apply"
    exit "$status"
    ;;
esac
