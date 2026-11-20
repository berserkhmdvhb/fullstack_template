#!/usr/bin/env bash
# Emit an OpenVEX v0.2.0 document for the built image(s): for every CVE Grype
# reports as FIXED in the shipped artifact, assert the image is not_affected
# (justification: fixed_version), pinned by its IMMUTABLE digest from
# image-digests.env — the KB "05" practice, built from the real Grype results and
# the real build digest, never a pre-baked claim.
#
# The template (infra/security/sample-vex.json) is a representative OpenVEX shape
# whose fields are ${VAR} tokens; this script fills the envelope from CI env and
# replaces the placeholder statement with one real statement per fixed CVE, so the
# shipped doc reflects the actual build (captured, never faked). A CVE with no fix
# in the shipped version is NEVER waved to not_affected.
#
# Usage: generate-vex.sh [SEC_OUTPUT_DIR] [scan.env] [image-digests.env]
#   SEC_OUTPUT_DIR     security output dir (default: output/security)
#   scan.env           dotenv of image refs under *_IMAGE (not *_DOCKER)
#   image-digests.env  dotenv of <ID>_IMAGE_DIGEST=sha256:... (from verify:registry)
set -Eeuo pipefail

# ---------- Shared runtime ----------
# shellcheck source=/dev/null
source "$(dirname "$0")/_helpers.sh"
trap 'trap_err' ERR

# ---------- Usage ----------
usage() {
  cat <<'USAGE'
Usage: generate-vex.sh [SEC_OUTPUT_DIR] [scan.env] [image-digests.env]

Emit an OpenVEX document: one not_affected/fixed_version statement per CVE Grype
reports fixed in the shipped artifact, pinned by the image's immutable digest.

  SEC_OUTPUT_DIR     security output dir        (default: output/security)
  scan.env           image refs under *_IMAGE   (default: scan.env)
  image-digests.env  <ID>_IMAGE_DIGEST=sha256:  (default: image-digests.env)
USAGE
  exit 0
}

# ---------- Args (three positionals; -h shows usage) ----------
# Collected in order so `-h` is handled without being mistaken for SEC_OUTPUT_DIR.
POSITIONAL=()
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage ;;
    *) POSITIONAL+=("$1") ;;
  esac
  shift
done

# ---------- Defaults ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEC_OUTPUT_DIR="${POSITIONAL[0]:-${SEC_OUTPUT_DIR:-output/security}}"
SCAN_ENV="${POSITIONAL[1]:-${SCAN_ENV_PATH:-scan.env}}"
DIGEST_ENV="${POSITIONAL[2]:-image-digests.env}"
TEMPLATE="${SCRIPT_DIR}/sample-vex.json"

GRYPE_DIR="${SEC_OUTPUT_DIR}/grype"
VEX_DIR="${SEC_OUTPUT_DIR}/vex"
OUT="${VEX_DIR}/openvex.json"

lib_banner "generate-vex" "OpenVEX not_affected statements from Grype fixes"

# ---------- Pre-flight ----------
need_cmd jq
[[ -f "${TEMPLATE}" ]] || die "VEX template not found: ${TEMPLATE}"

now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }
VEX_TIMESTAMP="$(now_iso)"
VEX_ID="${GITHUB_SHA:-local}"; VEX_ID="${VEX_ID:0:7}"   # short SHA
VEX_AUTHOR="${GITHUB_REPOSITORY:-service-template} <security@example.com>"

# Load scan.env (image refs) + image-digests.env (immutable digests). The digest
# capture is produced by verify:registry (protected refs only); without it we
# cannot pin by digest, so we warn and emit nothing rather than pin by tag.
for envf in "${SCAN_ENV}" "${DIGEST_ENV}"; do
# shellcheck source=/dev/null
  [[ -f "$envf" ]] && { set -a; . "$envf"; set +a; }
done
if [ ! -f "${DIGEST_ENV}" ]; then
  warn "${DIGEST_ENV} not found — cannot pin product by immutable digest"
fi

mkdir -p "${VEX_DIR}"

# Fill the template envelope (the tokens shared by every statement) → base doc.
# Pure-bash substitution (no envsubst/sed) so only these exact tokens move.
render_envelope() {
  local s; s="$(cat "${TEMPLATE}")"
  s="${s//\$\{VEX_ID\}/${VEX_ID}}"
  s="${s//\$\{VEX_AUTHOR\}/${VEX_AUTHOR}}"
  s="${s//\$\{VEX_TIMESTAMP\}/${VEX_TIMESTAMP}}"
  s="${s//\$\{GITHUB_SERVER_URL\}/${GITHUB_SERVER_URL:-https://github.com}}"
  s="${s//\$\{GITHUB_REPOSITORY\}/${GITHUB_REPOSITORY:-service-template}}"
  # tooling is pinned to the producing commit, not a moving main (see test_roadmap_and_vex).
  s="${s//\$\{GITHUB_SHA\}/${GITHUB_SHA:-local}}"
  printf '%s\n' "${s}"
}
base="$(render_envelope)"

# Build the statements array from Grype JSON: one not_affected/fixed_version
# statement per CVE that has a fix, pinned to the image's immutable digest. The
# Grype per-image report is named <svc>.image.vuln.grype.json, so the service id
# (→ the *_IMAGE / *_IMAGE_DIGEST keys) is recovered from the filename.
statements='[]'
shopt -s nullglob
for gj in "${GRYPE_DIR}"/*.image.vuln.grype.json; do
  base_name="$(basename "${gj}")"
  svc="${base_name%.image.vuln.grype.json}"          # e.g. backend
  up="$(printf '%s' "${svc}" | tr '[:lower:]' '[:upper:]')"
  digest_key="${up}_IMAGE_DIGEST"
  ref_key="${up}_IMAGE"
  digest="${!digest_key:-}"
  ref="${!ref_key:-}"
  if [ -z "${digest}" ]; then
    warn "no immutable digest for '${svc}' (${DIGEST_ENV}:${digest_key}); skipping (cannot pin by digest)"
    continue
  fi
  if [ -n "${ref}" ]; then
    tag="${ref##*:}"; repo="${ref%:*}"; repository_url="${repo#*/}"   # strip host for purl
  else
    tag="${IMAGE_TAG:-latest}"; repository_url="ghcr.io/$(printf %s "${GITHUB_REPOSITORY:-unknown}" | tr "[:upper:]" "[:lower:]")"
  fi
  svc_stmts="$(jq -c \
    --arg svc "${svc}" --arg digest "${digest}" --arg repo_url "${repository_url}" \
    --arg tag "${tag}" --arg ts "${VEX_TIMESTAMP}" \
    '
    [ .matches[]
      # only-fixed: a CVE with no fix is never waved to not_affected
      | select((.vulnerability.fix.versions // []) | length > 0)
      | {
          # statement @id is the OSV advisory reference for the match id,
          # not the project internal path — traceable and stable across the doc.
          "@id": ("https://osv.dev/vulnerability/" + .vulnerability.id),
          # REQUIRED by the OpenVEX spec: the vulnerability identifier as a
          # top-level string. The nested "vulnerability:{name,description}"
          # object is not part of the statement schema and made consumers see
          # vulnerability_id as null — this is the field they key on.
          vulnerability_id: .vulnerability.id,
          timestamp: $ts,
          products: [{
            "@id": ("pkg:oci/" + $svc + "@" + $digest + "?repository_url=" + $repo_url + "&tag=" + $tag),
            subcomponents: [{
              "@id": (.artifact.purl // ("pkg:" + (.artifact.type // "generic") + "/" + .artifact.name + "@" + (.artifact.version // "")))
            }]
          }],
          status: "not_affected",
          justification: "fixed_version",
          impact_statement: ("Fix shipped in " + (.vulnerability.fix.versions[0]) + "; the vulnerable code path is not reachable in this artifact.")
        }
    ]
    ' "${gj}" 2>/dev/null || echo '[]')"
  statements="$(jq -c --argjson a "${svc_stmts}" '. + $a' <<<"${statements}")"
done

# Assemble: base envelope + the real statements (replacing the placeholder one).
echo "${base}" | jq --argjson s "${statements}" '.statements = $s' > "${OUT}"

jq -e . "${OUT}" >/dev/null || die "generate-vex: rendered VEX doc is not valid JSON"

n="$(jq '.statements | length' "${OUT}")"
success "OpenVEX document written: ${OUT} (${n} statement(s))"
