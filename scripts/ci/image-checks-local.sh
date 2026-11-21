#!/usr/bin/env bash
# image-checks-local.sh — build each image in .github/container-images.yml with a throwaway tag, run
# scripts/ci/image-checks.sh --strict on it (the same call the `images` job in ci.yml makes), and remove
# the images. `make image-checks` runs it.
#
# Usage: image-checks-local.sh [<image-id>...]    (default: every image in the manifest)
#
# The build arguments are the manifest's own, rendered by .github/scripts/container_matrix.py exactly as in
# CI, plus local label values (a reserved .invalid source URL, HEAD's commit and time), so the strict label
# checks hold. Needs docker, jq and Python with PyYAML (the project venv has it); IMAGE_CHECKS_MANIFEST
# overrides the manifest path.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PATH="$root/.tools/bin:$PATH"
IMAGE_CHECKS_MANIFEST="${IMAGE_CHECKS_MANIFEST:-$root/.github/container-images.yml}"
export IMAGE_CHECKS_MANIFEST
cd "$root"

python="python3"
if [[ -x "$root/.venv/bin/python" ]]; then python="$root/.venv/bin/python"; fi
head_sha="$(git rev-parse HEAD)"
matrix="$(GITHUB_REF_NAME=local GITHUB_REF_TYPE=branch GITHUB_SHA="$head_sha" \
  GITHUB_REPOSITORY_OWNER=local GITHUB_REPOSITORY=local/local \
  "$python" .github/scripts/container_matrix.py --matrix --config "$IMAGE_CHECKS_MANIFEST" | head -n 1)"

ids=("$@")
if ((${#ids[@]} == 0)); then
  mapfile -t ids < <(jq -r '.include[].id' <<<"$matrix")
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
  entry="$(ID="$id" jq -c '.include[] | select(.id == env.ID)' <<<"$matrix")"
  if [[ -z "$entry" ]]; then
    echo "::error::no image '${id}' in the manifest"
    rc=1
    continue
  fi
  ref="image-checks-local/${id}:$$"
  args=()
  while IFS= read -r arg; do args+=(--build-arg "$arg"); done \
    < <(jq -r '(.build_args // {}) | to_entries[] | "\(.key)=\(.value)"' <<<"$entry")
  args+=(--build-arg "SOURCE_URL=https://example.invalid/local/image-checks"
    --build-arg "VCS_REF=${head_sha}" --build-arg "VCS_BRANCH=local" --build-arg "BUILD_DATE=${created}")
  built+=("$ref")
  docker buildx build --load --file "$(jq -r '.dockerfile' <<<"$entry")" --tag "$ref" \
    "${args[@]}" "$(jq -r '.context' <<<"$entry")"
  bash scripts/ci/image-checks.sh --strict "$id" "$ref" || rc=1
done
# The last command is the exit status (a bare `exit` after the trap trips shellcheck SC2329).
[[ "$rc" -eq 0 ]]
