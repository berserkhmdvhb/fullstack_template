#!/usr/bin/env bash
# hadolint.sh — lint every tracked Dockerfile (backs the `hadolint` job in .github/workflows/ci.yml
# and the pre-commit hook). Uses .hadolint.yaml at the repo root when present.
set -euo pipefail

mapfile -t files < <(git ls-files 'Dockerfile*' '*/Dockerfile*' '*.Dockerfile' | LC_ALL=C sort -u)
if (( ${#files[@]} == 0 )); then
  echo "hadolint: no tracked Dockerfiles" >&2
  exit 1
fi
echo "hadolint: ${#files[@]} Dockerfile(s)"
hadolint "${files[@]}"
