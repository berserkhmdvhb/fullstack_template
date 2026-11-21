#!/usr/bin/env bash
# Print the version and the two Subresource Integrity hashes for a swagger-ui-dist release.
# Usage: scripts/swagger-ui-sri.sh 5.35.1
# Paste the output into src/your_service_name/backend/api/docs_page.py (wiki/backend-architecture.md).
# Fails (non-zero, nothing printed) when a download fails: no hash of an empty or error body.
set -Eeuo pipefail

version="${1:?usage: swagger-ui-sri.sh <swagger-ui-dist version, for example 5.35.1>}"
[[ "${version}" =~ ^5\.[0-9]+\.[0-9]+$ ]] || { echo "not an exact 5.x.y version: ${version}" >&2; exit 2; }

sri() {
  local body
  # A variable assignment keeps curl's exit status visible to set -e (a pipeline into openssl would hide it).
  body="$(mktemp)"
  trap 'rm -f "${body}"' RETURN
  # `|| return 1`: this runs inside $(...), where set -e does not reach, so a partial body is never hashed.
  curl -fsS --proto '=https' --tlsv1.2 -o "${body}" "https://cdn.jsdelivr.net/npm/swagger-ui-dist@${version}/$1" || return 1
  [[ -s "${body}" ]] || { echo "empty download: $1" >&2; return 1; }
  openssl dgst -sha384 -binary < "${body}" | openssl base64 -A
}

css="$(sri swagger-ui.css)"
js="$(sri swagger-ui-bundle.js)"

echo "SWAGGER_UI_VERSION = \"${version}\""
echo "SWAGGER_UI_CSS_SRI = \"sha384-${css}\""
echo "SWAGGER_UI_JS_SRI  = \"sha384-${js}\""
