#!/usr/bin/env bash
# verify-base-images.sh — verify the vendor's keyless signature of every distroless base image the Dockerfiles
# pin by digest, before the build. A read: no push, no signing, no secret, no login (a public registry, Rekor, TUF).
#
# One source of digests: the `FROM <image>@sha256:<digest>` lines of the Dockerfiles. A gcr.io/distroless base is
# verified with cosign; a base listed in unsigned-base-images.tsv (with its reason) is skipped by name; any other
# base, an unpinned FROM and an unparsable FROM fail. A stage alias (`FROM runtime-base`) and `scratch` are skipped.
#
# Usage: verify-base-images.sh [--list] [Dockerfile ...]   (default: ./Dockerfile*)
# Env:   COSIGN (default cosign), BASE_IMAGES_UNSIGNED (default: the .tsv next to this script)
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cosign_bin="${COSIGN:-cosign}"
unsigned_file="${BASE_IMAGES_UNSIGNED:-${here}/unsigned-base-images.tsv}"
# The distroless README: keyless signatures by this service account, issued by Google's OIDC.
identity="keyless@distroless.iam.gserviceaccount.com"
issuer="https://accounts.google.com"

list=false
if [[ "${1:-}" == "--list" ]]; then list=true; shift; fi
files=("$@")
if [[ ${#files[@]} -eq 0 ]]; then files=(Dockerfile*); fi

failures=0
fail() { echo "::error::$*" >&2; failures=$((failures + 1)); }

# Print "<file>\t<ref>" for every FROM that names an image; "<file>\t!<line>" for one that does not parse.
parse() {
  local file="$1" line tok ref
  local -a words
  local -A stages=()
  while IFS= read -r line || [[ -n "${line}" ]]; do
    [[ "${line}" =~ ^[[:space:]]*[Ff][Rr][Oo][Mm][[:space:]] ]] || continue
    read -r -a words <<<"${line}"
    ref=""
    for tok in "${words[@]:1}"; do
      case "${tok}" in --*) continue ;; esac
      ref="${tok}"
      break
    done
    # The alias after AS (any case) names a stage that later FROMs may use.
    for ((i = 2; i < ${#words[@]}; i++)); do
      if [[ "${words[i]}" =~ ^[Aa][Ss]$ && -n "${words[i + 1]:-}" ]]; then stages["${words[i + 1]}"]=1; fi
    done
    if [[ -z "${ref}" || "${ref}" == *'$'* ]]; then printf '%s\t!%s\n' "${file}" "${line}"; continue; fi
    if [[ -n "${stages[${ref}]:-}" || "${ref}" == "scratch" ]]; then continue; fi
    printf '%s\t%s\n' "${file}" "${ref}"
  done <"${file}"
}

is_unsigned() { # repository -> 0 when listed in the data file
  awk -F'\t' -v r="$1" '!/^#/ && $1 == r { found = 1 } END { exit !found }' "${unsigned_file}"
}

for f in "${files[@]}"; do
  [[ -f "${f}" ]] || { fail "${f}: no such Dockerfile"; continue; }
  while IFS=$'\t' read -r file ref; do
    if [[ "${ref}" == '!'* ]]; then fail "${file}: unparsable FROM: ${ref#!}"; continue; fi
    if [[ "${list}" == true ]]; then printf '%s\t%s\n' "${file}" "${ref}"; continue; fi
    if [[ ! "${ref}" =~ ^([^@]+)@sha256:[0-9a-f]{64}$ ]]; then fail "${file}: ${ref} is not pinned by digest"; continue; fi
    name="${BASH_REMATCH[1]}"
    repo="${name%%:*}"
    # A registry port (host:5000/img) holds a colon before the last slash: keep it.
    if [[ "${name}" == */* && "${name##*/}" != *:* ]]; then repo="${name}"; fi
    if [[ "${repo}" == gcr.io/distroless/* ]]; then
      if out="$("${cosign_bin}" verify --certificate-identity "${identity}" --certificate-oidc-issuer "${issuer}" "${ref}" 2>&1 >/dev/null)"; then
        echo "verified: ${ref}"
      else
        fail "${file}: the signature of ${ref} did not verify (identity ${identity}): ${out##*$'\n'}"
      fi
    elif is_unsigned "${repo}"; then
      echo "skipped (no vendor signature, see $(basename "${unsigned_file}")): ${ref}"
    else
      fail "${file}: ${ref} is neither distroless nor listed in $(basename "${unsigned_file}"): classify it"
    fi
  done < <(parse "${f}")
done

if [[ "${failures}" -gt 0 ]]; then echo "base image verification failed (${failures})" >&2; exit 1; fi
