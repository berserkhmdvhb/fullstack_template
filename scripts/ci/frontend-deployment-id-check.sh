#!/usr/bin/env bash
# frontend-deployment-id-check.sh — build the frontend image with a dotted VERSION and a commit VCS_REF,
# run it, and check that a real page carries the sanitised deployment id (version + first 12 characters of
# the commit, anything Next.js refuses turned into _) on its asset URLs. Throwaway tag and container
# names, bound to loopback on a free port, removed by exact name on exit. `make frontend-deployment-id`
# runs it. Needs docker and curl.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$root"

version="1.2.3+g1a2b"
commit="0123456789abcdef0123456789abcdef01234567"
expected="1_2_3_g1a2b-0123456789ab"
tag="frontend-deployment-id-check:$$"
name="frontend-deployment-id-check-$$"

cleanup() {
  docker rm -f "$name" > /dev/null 2>&1 || true
  docker rmi "$tag" > /dev/null 2>&1 || true
}
trap cleanup EXIT

docker build --file Dockerfile.frontend --tag "$tag" \
  --build-arg "VERSION=${version}" --build-arg "VCS_REF=${commit}" .
docker run --detach --name "$name" --publish 127.0.0.1::3000 "$tag" > /dev/null
port="$(docker port "$name" 3000/tcp | sed -n '1s/.*://p')"

page=""
for _ in $(seq 1 60); do
  if page="$(curl -fsS --max-time 3 "http://127.0.0.1:${port}/" 2> /dev/null)"; then break; fi
  sleep 1
done
[[ -n "$page" ]] || { echo "ERROR: the frontend image did not serve a page" >&2; exit 1; }

grep -q "data-dpl-id=\"${expected}\"" <<<"$page" \
  || { echo "ERROR: the page has no data-dpl-id=\"${expected}\"" >&2; exit 1; }
grep -Eq "/_next/static/[^\"\\\\]+\\.js\\?dpl=${expected}" <<<"$page" \
  || { echo "ERROR: no asset URL carries ?dpl=${expected}" >&2; exit 1; }
echo "ok: the page and its asset URLs carry the deployment id ${expected}"
