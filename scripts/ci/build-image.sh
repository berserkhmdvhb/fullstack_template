#!/usr/bin/env bash
# build-image.sh — build one image of .github/container-images.yml into the local docker daemon. The one
# build path of the pull request `images` job (ci.yml), `make image-checks` and the kind job: no push, no
# registry login, no build cache.
#
# Usage: build-image.sh <matrix-entry-json> <tag>
#   matrix-entry-json  one entry of `container_matrix.py --matrix` (id, dockerfile, context, build_args)
# Env: SOURCE_URL, VCS_REF (a commit; its commit time is BUILD_DATE) and VCS_BRANCH, the label values the
#      release build passes too; PLATFORM (optional) builds that platform.
set -euo pipefail

[[ $# -eq 2 ]] || { echo "usage: build-image.sh <matrix-entry-json> <tag>" >&2; exit 2; }
entry="$1" ref="$2"
: "${SOURCE_URL:?SOURCE_URL is required}" "${VCS_REF:?VCS_REF is required}" "${VCS_BRANCH:?VCS_BRANCH is required}"
created="$(TZ=UTC git show -s --format=%cd --date=format-local:%Y-%m-%dT%H:%M:%SZ "${VCS_REF}")"
args=()
while IFS= read -r arg; do args+=(--build-arg "${arg}"); done \
  < <(jq -r '(.build_args // {}) | to_entries[] | "\(.key)=\(.value)"' <<<"${entry}")
args+=(--build-arg "SOURCE_URL=${SOURCE_URL}" --build-arg "VCS_REF=${VCS_REF}" \
  --build-arg "VCS_BRANCH=${VCS_BRANCH}" --build-arg "BUILD_DATE=${created}")
if [[ -n "${PLATFORM:-}" ]]; then args+=(--platform "${PLATFORM}"); fi
docker buildx build --load --file "$(jq -r '.dockerfile' <<<"${entry}")" --tag "${ref}" "${args[@]}" \
  "$(jq -r '.context' <<<"${entry}")"
