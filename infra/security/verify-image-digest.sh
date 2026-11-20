#!/usr/bin/env bash
# Verify that a built image's captured immutable digest (from verify:registry's
# image-digests.env) matches the digest of an image actually present somewhere
# (a deployed/pulled `repository@sha256:…`). This is a Compose/main-flavour aid:
# that flavour has no helm `deploy:verify` to pin the deploy by digest, so this
# is how you confirm a running image is the one that was signed + attested — it
# is NOT admission enforcement (see wiki/ROADMAP-supply-chain.md for the Kyverno
# control that would enforce this at deploy time).
#
# Usage:
#   verify-image-digest.sh --digest <KEY> --actual <repository@sha256:...> [--env <file>]
#     --digest KEY   the <ID>_IMAGE_DIGEST key to read from the digest env
#     --actual REF   the image reference to check (tag or digest form)
#     --env FILE     the digest env (default: image-digests.env in $PWD)
#
# Exit 0 if the captured digest equals the digest in --actual; non-zero otherwise
# (missing key, missing digest, or mismatch) — each with a clear message.
set -Eeuo pipefail

# Shared security runtime: CI-clean logging/banners (colour degrades off on a
# non-TTY, so piped CI logs stay clean text) and the ERR trap. This is a
# read-only verification *aid* — it compares the captured digest against a
# running image's digest as a plain string check: no crypto, no deploy-time
# blocking (see wiki/ROADMAP-supply-chain.md for the Kyverno control that would
# enforce this at deploy time).
# shellcheck source=/dev/null
source "$(dirname "$0")/_helpers.sh"
trap 'trap_err' ERR

DIGEST_ENV="${DIGEST_ENV:-image-digests.env}"
KEY=""
ACTUAL=""

lib_banner "verify-image-digest" "Confirm a running image matches the captured digest"

while [ $# -gt 0 ]; do
  case "$1" in
    --digest) KEY="${2:-}"; shift 2 ;;
    --actual) ACTUAL="${2:-}"; shift 2 ;;
    --env)    DIGEST_ENV="${2:-}"; shift 2 ;;
    *) error "verify-image-digest: unknown argument: $1"; exit 2 ;;
  esac
done

if [ -z "${KEY}" ] || [ -z "${ACTUAL}" ]; then
  error "usage: verify-image-digest.sh --digest <KEY> --actual <repository@sha256:...> [--env <file>]"
  exit 2
fi
if [ ! -f "${DIGEST_ENV}" ]; then
  error "digest env not found: ${DIGEST_ENV}"
  exit 2
fi

# Read the captured digest for KEY from the dotenv (safe: KEY is [A-Z0-9_]).
set -a
# shellcheck disable=SC1090
. "${DIGEST_ENV}"
set +a
CAPTURED="$(printf '%s' "${!KEY:-}" | sed -n 's/^.*\(sha256:[0-9a-f]\{64\}\).*/\1/p' | head -n1)"
if [ -z "${CAPTURED}" ]; then
  error "no captured digest for ${KEY} in ${DIGEST_ENV}"
  exit 1
fi

# The digest is the part after '@' in a canonical ref; tolerate a bare sha256:… too.
ACTUAL_DIGEST="$(printf '%s' "${ACTUAL}" | sed -n 's/^.*\(sha256:[0-9a-f]\{64\}\).*/\1/p' | head -n1)"
if [ -z "${ACTUAL_DIGEST}" ]; then
  error "--actual has no sha256 digest: ${ACTUAL}"
  exit 2
fi

if [ "${CAPTURED}" = "${ACTUAL_DIGEST}" ]; then
  success "OK: ${KEY} digest matches ${ACTUAL}"
  exit 0
fi
error "MISMATCH: ${KEY} captured ${CAPTURED} but --actual is ${ACTUAL_DIGEST}"
exit 1
