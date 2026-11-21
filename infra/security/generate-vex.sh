#!/usr/bin/env bash
# Emit an OpenVEX v0.2.0 document for the built image(s): every finding Grype
# reports (.matches and .ignoredMatches; an ignore in grype.yaml only stops the
# gate, the component is still in the image) becomes an `affected` statement with
# an action_statement (OpenVEX requires one for `affected`): "Upgrade <pkg> to <fix>
# or later" when Grype names a fix, else a no-fix note. A fix existing upstream
# never makes the shipped image not_affected. `not_affected` comes ONLY from the
# hand-reviewed list infra/security/local/vex-not-affected.json (entries carry a
# justification, an impact_statement, a review date and an `until` expiry; an
# expired entry is ignored with a warning, a malformed one fails the script).
# Statements are pinned by the IMMUTABLE digest from image-digests.env, built from
# the real Grype results and the real build digest, never a pre-baked claim.
# Spec: https://github.com/openvex/spec/blob/main/OPENVEX-SPEC.md
#
# The template (infra/security/sample-vex.json) is a representative OpenVEX shape
# whose fields are ${VAR} tokens; this script fills the envelope from CI env and
# replaces the placeholder statement with the real statements, so the shipped doc
# reflects the actual build (captured, never faked).
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

Emit an OpenVEX document: one `affected` statement (with an action_statement) per
Grype finding, pinned by the image's immutable digest; `not_affected` only for
entries of infra/security/local/vex-not-affected.json (override: VEX_NOT_AFFECTED_LIST).

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
NOT_AFFECTED_LIST="${VEX_NOT_AFFECTED_LIST:-${SCRIPT_DIR}/local/vex-not-affected.json}"

GRYPE_DIR="${SEC_OUTPUT_DIR}/grype"
VEX_DIR="${SEC_OUTPUT_DIR}/vex"
OUT="${VEX_DIR}/openvex.json"

lib_banner "generate-vex" "OpenVEX statements from Grype findings"

# ---------- Pre-flight ----------
need_cmd jq
[[ -f "${TEMPLATE}" ]] || die "VEX template not found: ${TEMPLATE}"
[[ -f "${NOT_AFFECTED_LIST}" ]] || die "not_affected list not found: ${NOT_AFFECTED_LIST}"

now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }
VEX_TIMESTAMP="$(now_iso)"
VEX_ID="${GITHUB_SHA:-local}"; VEX_ID="${VEX_ID:0:7}"   # short SHA
VEX_AUTHOR="${GITHUB_REPOSITORY:-service-template} <security@example.com>"
TODAY="$(date -u +%Y-%m-%d)"

# Validate the reviewed not_affected list; any problem fails the script. Justifications
# are OpenVEX's five (spec: "Status Justifications"); dates are YYYY-MM-DD.
JUSTIFICATIONS='["component_not_present","vulnerable_code_not_present","vulnerable_code_not_in_execute_path","vulnerable_code_cannot_be_controlled_by_adversary","inline_mitigations_already_exist"]'
# Builtins used by the date check (all in jq 1.6+): test, strptime("%Y-%m-%d"), mktime, todate.
problems="$(jq -rs --argjson just "${JUSTIFICATIONS}" --arg today "${TODAY}" '
  def isdate: type == "string" and test("^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    and ((try (strptime("%Y-%m-%d") | mktime | todate[0:10]) catch null) == .);
  def blank: type != "string" or (test("\\S") | not);
  if length != 1 or (.[0] | type) != "array" then "the not_affected list must be one JSON array"
  else .[0] as $l
    | ( $l | to_entries[] | .key as $i | .value as $e
        | if ($e | type) != "object" then "entry \($i): not an object"
          else
            ( ["vulnerability","service","package","justification","impact_statement"][]
              | . as $k | select($e[$k] | blank) | "entry \($i): missing or empty \($k)" ),
            ( select(($e.justification | type) == "string" and ($e.justification | blank | not)
                     and (($just | index([$e.justification])) == null))
              | "entry \($i): unknown justification \($e.justification)" ),
            ( ["reviewed","until"][] | . as $k | select(($e[$k] | isdate) | not)
              | "entry \($i): \($k) must be a valid YYYY-MM-DD date" ),
            ( select(($e.reviewed | isdate) and ($e.until | isdate) and $e.reviewed > $e.until)
              | "entry \($i): reviewed is after until" ),
            ( select(($e.reviewed | isdate) and $e.reviewed > $today)
              | "entry \($i): reviewed is in the future" )
          end ),
      ( [ $l[] | select(type == "object") | [.vulnerability, .service, .package] ]
        | group_by(.)[] | select(length > 1)
        | "duplicate entry for \(.[0][0]) (service \(.[0][1]), package \(.[0][2]))" )
  end' "${NOT_AFFECTED_LIST}")" || die "not_affected list is not valid JSON: ${NOT_AFFECTED_LIST}"
if [ -n "${problems}" ]; then
  printf '%s\n' "${problems}" >&2
  die "invalid not_affected list: ${NOT_AFFECTED_LIST}"
fi
# An entry is valid through the end of its `until` day; it expires when today > until.
while IFS= read -r line; do
  [ -n "${line}" ] && warn "not_affected entry expired (until ${line}); ignored, the finding stays affected"
done < <(jq -r --arg today "${TODAY}" '.[] | select(.until < $today) | "\(.until) \(.vulnerability) \(.service) \(.package)"' "${NOT_AFFECTED_LIST}")
used='[]'   # indexes of list entries that matched a finding

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

# Build the statements array from Grype JSON: one statement per finding (.matches
# and .ignoredMatches), `affected` unless a live reviewed-list entry says
# not_affected, pinned to the image's immutable digest. The
# Grype per-image report is named <svc>.image.vuln.grype.json, so the service id
# (→ the *_IMAGE / *_IMAGE_DIGEST keys) is recovered from the filename.
# Grype JSON shape (v0.121): .matches[] / .ignoredMatches[] with .vulnerability.{id,fix.versions}
# and .artifact.{name,version,type,purl}; https://github.com/anchore/grype (grype/presenter/models).
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
  # Shape check (Grype v0.121 document: .matches always an array, .ignoredMatches absent or an array
  # of objects) so no iteration below can silently skip a wrong-typed field.
  # Slurped: exactly one document. ignoredMatches may be absent or null, else an array.
  jq -se 'length == 1 and (.[0] | type == "object" and (.matches | type) == "array"
    and ((.ignoredMatches | type) as $t | $t == "null" or $t == "array")
    and all(.matches[], (.ignoredMatches // [])[]; type == "object"))' "${gj}" >/dev/null \
    || die "cannot build VEX statements from ${gj} (not a Grype JSON report)"
  res="$(jq -c \
    --arg svc "${svc}" --arg digest "${digest}" --arg repo_url "${repository_url}" \
    --arg tag "${tag}" --arg ts "${VEX_TIMESTAMP}" --arg today "${TODAY}" \
    --slurpfile list "${NOT_AFFECTED_LIST}" \
    '
    ($list[0] | to_entries | map(select(.value.service == $svc and .value.until >= $today))) as $live
    | [ (.matches[], (.ignoredMatches // [])[])
        | . as $m
        | (.vulnerability.fix.versions // []) as $fix
        | (.vulnerability.id | if type == "string" and . != "" then . else error("finding without a vulnerability id") end) as $vid
        | ((.artifact.name | select(type == "string" and test("[^\\s]"))) // (.artifact.purl | select(type == "string" and test("[^\\s]"))) // "the affected package") as $pkg
        | ($live | map(select(.value.vulnerability == $m.vulnerability.id and .value.package == $m.artifact.name)) | first) as $na
        | {
            # statement @id is the OSV advisory reference for the match id,
            # not the project internal path — traceable and stable across the doc.
            "@id": ("https://osv.dev/vulnerability/" + $vid),
            # REQUIRED by the OpenVEX spec: the vulnerability identifier as a
            # top-level string; this is the field consumers key on.
            vulnerability_id: $vid,
            timestamp: $ts,
            products: [{
              "@id": ("pkg:oci/" + $svc + "@" + $digest + "?repository_url=" + $repo_url + "&tag=" + $tag),
              subcomponents: [{
                "@id": (.artifact.purl // ("pkg:" + (.artifact.type // "generic") + "/" + .artifact.name + "@" + (.artifact.version // "")))
              }]
            }]
          }
          + (if $na then
              { status: "not_affected", justification: $na.value.justification, impact_statement: $na.value.impact_statement }
            else
              { status: "affected",
                action_statement: (if ($fix | length) > 0
                  then "Upgrade " + $pkg + " to " + ($fix[0] | if type == "string" then . else error("fix version is not a string") end) + " or later."
                  else "No upstream fix is available yet; the scheduled re-scan tracks it." end) }
            end)
          + { _used: ($na.key // null) }
      ]
    | unique_by([.vulnerability_id, .products[0].subcomponents[0]["@id"]]) as $all
    | { s: ($all | map(del(._used))), u: ($all | map(._used) | map(select(. != null))) }
    ' "${gj}")" || die "cannot build VEX statements from ${gj}"
  [ -n "${res}" ] || die "cannot build VEX statements from ${gj} (empty report)"
  svc_stmts="$(jq -c .s <<<"${res}")" || die "cannot build VEX statements from ${gj}"
  used="$(jq -c --argjson u "$(jq -c .u <<<"${res}")" '. + $u' <<<"${used}")" || die "cannot build VEX statements from ${gj}"
  statements="$(jq -c --argjson a "${svc_stmts}" '. + $a' <<<"${statements}")" || die "cannot build VEX statements from ${gj}"
done

# An entry that matched no finding produces no statement.
while IFS= read -r line; do
  [ -n "${line}" ] && log "not_affected entry matched no finding: ${line}"
done < <(jq -r --argjson used "${used}" --arg today "${TODAY}" \
  'to_entries[] | select(.value.until >= $today and ((.key | IN($used[])) | not)) | "\(.value.vulnerability) \(.value.service) \(.value.package)"' "${NOT_AFFECTED_LIST}")

# Assemble: base envelope + the real statements (replacing the placeholder one).
echo "${base}" | jq --argjson s "${statements}" '.statements = $s' > "${OUT}"

jq -e . "${OUT}" >/dev/null || die "generate-vex: rendered VEX doc is not valid JSON"

n="$(jq '.statements | length' "${OUT}")"
success "OpenVEX document written: ${OUT} (${n} statement(s))"
