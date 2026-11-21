#!/usr/bin/env bash
# rescan-scan.sh — the weekly re-scan of one image digest, on every platform of its index.
# Lists the platforms of ${IMAGE_REPOSITORY}@${IMAGE_DIGEST} (an index; a single manifest is scanned as is),
# runs grype per platform and merges the matches into findings/${IMAGE_SHORT}.json, the file
# scripts/rescan_report.py reads. grype exits 2 when it finds something at the config's fail-on severity:
# a finding, not an error. Registry auth comes from the GRYPE_REGISTRY_AUTH_* and docker login of the caller.
set -euo pipefail

: "${IMAGE_REPOSITORY:?}" "${IMAGE_DIGEST:?}" "${IMAGE_SHORT:?}"
ref="${IMAGE_REPOSITORY}@${IMAGE_DIGEST}"
config="${GRYPE_CONFIG:-infra/security/grype.yaml}"
mkdir -p findings parts

# os/arch[/variant] of every real manifest; attestation manifests carry the platform unknown/unknown.
mapfile -t platforms < <(docker buildx imagetools inspect --raw "${ref}" |
  jq -r '.manifests[]?.platform | select(. != null and .os != "unknown")
    | "\(.os)/\(.architecture)" + (if .variant then "/\(.variant)" else "" end)')
if ((${#platforms[@]} == 0)); then
  platforms=("")
fi

reports=()
for platform in "${platforms[@]}"; do
  out="parts/${IMAGE_SHORT}.${platform//\//_}.json"
  args=()
  if [[ -n "${platform}" ]]; then args=(--platform "${platform}"); fi
  rc=0
  grype "registry:${ref}" "${args[@]}" -c "${config}" -o json --file "${out}" || rc=$?
  if ((rc != 0 && rc != 2)) || ! jq -e '.matches' "${out}" >/dev/null; then
    echo "::error::grype failed on ${ref} ${platform} (exit ${rc})"
    exit 1
  fi
  reports+=("${out}")
done

key='[.vulnerability.id, .artifact.name, .artifact.version, .artifact.type]'
jq -s ".[0] + {
    matches: (map(.matches) | add | unique_by(${key})),
    ignoredMatches: (map(.ignoredMatches // []) | add | unique_by(${key}))
  }" "${reports[@]}" >"findings/${IMAGE_SHORT}.json"

echo "- \`${ref}\`: $(jq '.matches | length' "findings/${IMAGE_SHORT}.json") finding(s) over ${#reports[@]} platform(s)" >>"${GITHUB_STEP_SUMMARY:-/dev/null}"
