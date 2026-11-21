#!/usr/bin/env bash
# Print the version and the two Subresource Integrity hashes for a swagger-ui-dist release.
# Usage: scripts/swagger-ui-sri.sh 5.35.1
# Paste the output into src/your_service_name/backend/api/docs_page.py (wiki/backend-architecture.md).
set -Eeuo pipefail

version="${1:?usage: swagger-ui-sri.sh <swagger-ui-dist version, for example 5.35.1>}"
[[ "${version}" =~ ^5\.[0-9]+\.[0-9]+$ ]] || { echo "not an exact 5.x.y version: ${version}" >&2; exit 2; }

sri() {
  curl -fsS --proto '=https' --tlsv1.2 "https://cdn.jsdelivr.net/npm/swagger-ui-dist@${version}/$1" \
    | openssl dgst -sha384 -binary | openssl base64 -A
}

echo "SWAGGER_UI_VERSION = \"${version}\""
echo "SWAGGER_UI_CSS_SRI = \"sha384-$(sri swagger-ui.css)\""
echo "SWAGGER_UI_JS_SRI  = \"sha384-$(sri swagger-ui-bundle.js)\""
