#!/usr/bin/env bash
# image-checks-local.sh — build each image in .github/container-images.yml with a throwaway tag, run
# scripts/ci/image-checks.sh --strict on it (the same call the `images` job in ci.yml makes), and remove
# the images. `make image-checks` runs it.
#
# Usage: image-checks-local.sh [<image-id>...]    (default: every image in the manifest)
#
# The label build arguments get local values (a reserved .invalid source URL, HEAD's commit and time), so the
# strict label checks hold. Needs docker and yq (scripts/tools.sh install yq).
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PATH="$root/.tools/bin:$PATH"
manifest="$root/.github/container-images.yml"
cd "$root"

ids=("$@")
if ((${#ids[@]} == 0)); then
  mapfile -t ids < <(yq -r '.images[].id' "$manifest")
fi

built=()
cleanup() {
  local ref
  for ref in "${built[@]}"; do
    docker rmi "$ref" > /dev/null 2>&1 || true
  done
}
trap cleanup EXIT

created="$(TZ=UTC git show -s --format=%cd --date=format-local:%Y-%m-%dT%H:%M:%SZ HEAD)"
rc=0
for id in "${ids[@]}"; do
  ref="image-checks-local/${id}:$$"
  dockerfile="$(ID="$id" yq -r '.images[] | select(.id == strenv(ID)) | .dockerfile' "$manifest")"
  context="$(ID="$id" yq -r '.images[] | select(.id == strenv(ID)) | .context // "."' "$manifest")"
  if [[ -z "$dockerfile" ]]; then
    echo "::error::no image '${id}' in the manifest"
    rc=1
    continue
  fi
  built+=("$ref")
  docker buildx build --load --file "$dockerfile" --tag "$ref" \
    --build-arg "VERSION=0.0.0-local" \
    --build-arg "SOURCE_URL=https://example.invalid/local/image-checks" \
    --build-arg "VCS_REF=$(git rev-parse HEAD)" \
    --build-arg "VCS_BRANCH=local" \
    --build-arg "BUILD_DATE=${created}" \
    "$context"
  bash scripts/ci/image-checks.sh --strict "$id" "$ref" || rc=1
done
exit "$rc"
