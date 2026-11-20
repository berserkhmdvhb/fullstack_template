#!/bin/sh
# your-service-name — adaptable service template (see wiki/getting-started.md)
# Nginx entrypoint — render the config template, then exec nginx.
# ===================================================================
#
# `infra/nginx/nginx.conf` ships as an envsubst TEMPLATE (installed at
# /etc/nginx/nginx.conf.template): a handful of directives — the listen
# bind/port and the static-asset root — must stay configurable at runtime, and
# nginx does **not** expand environment variables inside its config on its own.
#
# This entrypoint:
#   1. applies runtime defaults (overridable via the ConfigMap / env),
#   2. renders only the allow-listed variables into a live config,
#   3. validates the rendered config with `nginx -t`,
#   4. creates the worker temp dirs the non-root worker needs, and
#   5. execs nginx in the foreground (`daemon off;`).
#
# Runs as the non-root `nginxuser`. POSIX sh — the nginx:alpine base has no bash.
# ===================================================================

set -eu

# ---- Fail-fast helpers --------------------------------------------------------
die() {
  echo "❌ $*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' is required but not available in this image"
}

# ---- Runtime defaults (overridable via the ConfigMap / env) ---------------------
: "${NGINX_BIND:=0.0.0.0}"
: "${NGINX_PORT:=8081}"
: "${FRONTEND_ROOT:=/usr/share/nginx/html}"
export NGINX_BIND NGINX_PORT FRONTEND_ROOT

TEMPLATE="/etc/nginx/nginx.conf.template"
RENDERED="/tmp/nginx.conf"

# ---- Banner -------------------------------------------------------------------
echo
echo "▶️  Nginx — static assets"
echo "   bind : ${NGINX_BIND}:${NGINX_PORT}"
echo "   root : ${FRONTEND_ROOT}"
echo "   conf : ${RENDERED} (rendered from ${TEMPLATE})"
echo

# ---- Preconditions ------------------------------------------------------------
need_cmd envsubst
need_cmd nginx

[ -n "${NGINX_PORT}" ] || die "NGINX_PORT is empty"
case "${NGINX_PORT}" in
  ''|*[!0-9]*) die "NGINX_PORT must be numeric (got '${NGINX_PORT}')" ;;
esac

[ -f "${TEMPLATE}" ] || die "nginx config template not found at ${TEMPLATE}"

# ---- Render the allow-listed variables into the live config -------------------
# envsubst reads the template from STDIN (it takes no input-file argument) and
# substitutes only the variables named in its first argument.
# shellcheck disable=SC2016  # envsubst takes its variable LIST literally — the single quotes are required
envsubst '${NGINX_BIND} ${NGINX_PORT} ${FRONTEND_ROOT}' \
  < "${TEMPLATE}" > "${RENDERED}"

# ---- Worker temp dirs (create BEFORE validating) ------------------------------
# The config declares client_body/proxy/fastcgi/... temp paths under /tmp/nginx/*,
# and `nginx -t` touches them even in test mode. Create them first so the non-root
# worker can write (the base image only pre-creates /var/cache/nginx).
mkdir -p /tmp/nginx/client_body /tmp/nginx/proxy /tmp/nginx/fastcgi \
         /tmp/nginx/uwsgi /tmp/nginx/scgi

# ---- Validate the rendered config before handing off --------------------------
# Fail fast with the parser output rather than letting nginx die at request time.
if ! nginx -t -c "${RENDERED}" 2>&1; then
  die "rendered nginx config failed validation: ${RENDERED}"
fi
echo "✅ nginx config validated: ${RENDERED}"

# ---- Hand off to nginx (foreground) -------------------------------------------
echo "▶️  Starting nginx..."
exec nginx -c "${RENDERED}" -g 'daemon off;'
