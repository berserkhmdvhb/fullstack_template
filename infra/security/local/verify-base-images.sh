#!/usr/bin/env bash
# verify-base-images.sh — verify the vendor's keyless signature of every distroless base image the Dockerfiles
# pin by digest, before the build. A read: no push, no signing, no secret, no login (a public registry, Rekor, TUF).
#
# One source of digests: the `FROM <image>@sha256:<digest>` lines of the Dockerfiles. A gcr.io/distroless base is
# verified with cosign; a base listed in unsigned-base-images.tsv (with its reason) is skipped by name; any other
# base, an unpinned FROM and an unparsable FROM fail. The same rules apply to `COPY/ADD --from=<image>` and
# `RUN --mount=from=<image>`. An earlier stage's alias, a stage index and `scratch` are skipped, and so is
# `<stage>-${TARGETARCH}` when both <stage>-amd64 and <stage>-arm64 are earlier stages and no ARG gives TARGETARCH a value.
#
# Usage: verify-base-images.sh [--list] [Dockerfile ...]
#   default files: every tracked Dockerfile (Dockerfile, Dockerfile.*, *.Dockerfile) outside tests/
# Env:   COSIGN (default cosign), BASE_IMAGES_UNSIGNED (default: the .tsv next to this script),
#        BASE_IMAGES_ATTEMPTS (default 3), BASE_IMAGES_BACKOFF (seconds, default 5; attempt n waits n times that)
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cosign_bin="${COSIGN:-cosign}"
unsigned_file="${BASE_IMAGES_UNSIGNED:-${here}/unsigned-base-images.tsv}"
attempts="${BASE_IMAGES_ATTEMPTS:-3}"
backoff="${BASE_IMAGES_BACKOFF:-5}"
# The distroless README: keyless signatures by this service account, issued by Google's OIDC.
identity="keyless@distroless.iam.gserviceaccount.com"
issuer="https://accounts.google.com"

list=false
if [[ "${1:-}" == "--list" ]]; then list=true; shift; fi
files=("$@")
if [[ ${#files[@]} -eq 0 ]]; then
  root="$(cd "${here}/../../.." && pwd)"
  cd "${root}"
  mapfile -t files < <(git ls-files -- '*Dockerfile*' \
    | grep -E '(^|/)(Dockerfile(\..*)?|[^/]*\.Dockerfile)$' | grep -Ev '(^|/)tests/' || true)
  [[ ${#files[@]} -gt 0 ]] || { echo "::error::no tracked Dockerfile found" >&2; exit 1; }
fi

failures=0
fail() { echo "::error::$*" >&2; failures=$((failures + 1)); }

# Print "<file>\t<ref>" for every image a Dockerfile pulls: FROM, COPY/ADD --from and RUN --mount=...from=;
# "<file>\t!<line>" for a FROM that does not parse. A stage alias, a stage index and `scratch` are skipped.
parse() {
  local file="$1" line tok ref i amd arm var alias
  local -a words refs
  local -A stages=()
  while IFS= read -r line || [[ -n "${line}" ]]; do
    read -r -a words <<<"${line}"
    [[ ${#words[@]} -gt 0 ]] || continue
    refs=() alias=""
    case "${words[0],,}" in
      arg)
        # A default for TARGETARCH would let `FROM <stage>-${TARGETARCH}` (below) name something else.
        for tok in "${words[@]:1}"; do
          if [[ "${tok}" == TARGETARCH=* ]]; then printf '%s\t!%s\n' "${file}" "${line}"; fi
        done ;;
      from)
        ref=""
        for tok in "${words[@]:1}"; do
          case "${tok}" in --*) continue ;; esac
          ref="${tok}"
          break
        done
        # The alias after AS (any case) names a stage that later instructions may use; it is registered after
        # this line's own ref is resolved, so a FROM can never name itself (FROM node AS node is an image).
        for ((i = 2; i < ${#words[@]}; i++)); do
          if [[ "${words[i]}" =~ ^[Aa][Ss]$ && -n "${words[i + 1]:-}" ]]; then alias="${words[i + 1],,}"; fi
        done
        # One variable is allowed, as `<stage>-${TARGETARCH}`, and only when both of its values (amd64, arm64)
        # name an earlier stage: the build then picks one of those stages, never an image.
        # shellcheck disable=SC2016 # the literal text ${TARGETARCH}, not an expansion
        var='${TARGETARCH}'
        amd="${ref//"${var}"/amd64}" arm="${ref//"${var}"/arm64}"
        if [[ "${ref}" =~ ^[a-z][a-z0-9_.-]*-\$\{TARGETARCH\}$ && -n "${stages[${amd,,}]:-}" && -n "${stages[${arm,,}]:-}" ]]; then
          ref=""
        elif [[ -z "${ref}" || "${ref}" == *'$'* ]]; then
          printf '%s\t!%s\n' "${file}" "${line}"; ref=""
        fi
        if [[ -n "${ref}" ]]; then refs+=("${ref}"); fi ;;
      copy | add)
        for tok in "${words[@]:1}"; do
          if [[ "${tok}" == --from=* ]]; then refs+=("${tok#--from=}"); fi
        done ;;
      run)
        for tok in "${words[@]:1}"; do
          if [[ "${tok}" == --mount=* && ",${tok#--mount=}," =~ ,from=([^,]+), ]]; then refs+=("${BASH_REMATCH[1]}"); fi
        done ;;
    esac
    for ref in "${refs[@]}"; do
      if [[ -n "${stages[${ref,,}]:-}" || "${ref}" == "scratch" || "${ref}" =~ ^[0-9]+$ ]]; then continue; fi
      printf '%s\t%s\n' "${file}" "${ref}"
    done
    if [[ -n "${alias}" ]]; then stages["${alias}"]=1; fi
  done <"${file}"
}

# Verify one ref with up to ${attempts} tries; sets $out to cosign's last output. Fails closed.
verify() {
  local ref="$1" n
  for ((n = 1; n <= attempts; n++)); do
    if out="$("${cosign_bin}" verify --certificate-identity "${identity}" --certificate-oidc-issuer "${issuer}" "${ref}" 2>&1 >/dev/null)"; then
      return 0
    fi
    if ((n < attempts)); then sleep "$((n * backoff))"; fi
  done
  return 1
}

is_unsigned() { # repository -> 0 when listed in the data file
  awk -F'\t' -v r="$1" '!/^#/ && $1 == r { found = 1 } END { exit !found }' "${unsigned_file}"
}

for f in "${files[@]}"; do
  [[ -f "${f}" ]] || { fail "${f}: no such Dockerfile"; continue; }
  while IFS=$'\t' read -r file ref; do
    if [[ "${ref}" == '!ARG'* || "${ref}" == '!arg'* ]]; then fail "${file}: an ARG may not set TARGETARCH: ${ref#!}"; continue; fi
    if [[ "${ref}" == '!'* ]]; then fail "${file}: unparsable FROM: ${ref#!}"; continue; fi
    if [[ "${list}" == true ]]; then printf '%s\t%s\n' "${file}" "${ref}"; continue; fi
    if [[ ! "${ref}" =~ ^([^@]+)@sha256:[0-9a-f]{64}$ ]]; then fail "${file}: ${ref} is not pinned by digest"; continue; fi
    name="${BASH_REMATCH[1]}"
    repo="${name%%:*}"
    # A registry port (host:5000/img) holds a colon before the last slash: keep it.
    if [[ "${name}" == */* && "${name##*/}" != *:* ]]; then repo="${name}"; fi
    if [[ "${repo}" == gcr.io/distroless/* ]]; then
      if verify "${ref}"; then
        echo "verified: ${ref}"
      elif grep -Eiq 'tuf|timeout|timed out|deadline exceeded|connection (refused|reset)|no such host|dial tcp|temporary failure|unexpected eof|tls|\b(429|500|502|503|504)\b|too many requests|rekor.*(unavailable|error)' <<<"${out}"; then
        fail "${file}: the signature of ${ref} could not be checked, the registry or Sigstore could not be reached (${attempts} attempts):"$'\n'"${out}"
      else
        fail "${file}: the signature of ${ref} did not verify (identity ${identity}, ${attempts} attempts):"$'\n'"${out}"
      fi
    elif is_unsigned "${repo}"; then
      echo "skipped (no vendor signature, see $(basename "${unsigned_file}")): ${ref}"
    else
      fail "${file}: ${ref} is neither distroless nor listed in $(basename "${unsigned_file}"): classify it"
    fi
  done < <(parse "${f}")
done

if [[ "${failures}" -gt 0 ]]; then echo "base image verification failed (${failures})" >&2; exit 1; fi
