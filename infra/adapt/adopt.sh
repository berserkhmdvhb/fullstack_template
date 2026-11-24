#!/usr/bin/env bash
# infra/adapt/adopt.sh
# =============================================================================
# Config-driven template adoption. THIN wrapper over the two Python adapters:
#   * replace_service_name.py -- the service NAME (identity: package, image
#     suffix, k8s name prefix, BACKEND_ENTRY, CI package_name)
#   * adopt_stamp.py          -- the deployment COORDINATES (ingress domain +
#     image registry)
# Both read the same adopt.conf, so editing that one file applies everywhere.
# Re-execs under bash, validates the config, runs the name adapter then the
# stamping step (name first: the two are independent and commute), and forwards
# --dry-run. Mirrors the local runner's wrapper pattern (infra/security/local).
#
# Usage:
#   infra/adapt/adopt.sh            # apply adopt.conf
#   infra/adapt/adopt.sh --dry-run  # preview (touches nothing)
#
# Dry-run first. Real runs back up every touched file to
# .adapt-backup/<timestamp>/ (gitignored) before writing. SERVICE_NAME must be a
# lowercase identifier [a-z][a-z0-9_]*.
# =============================================================================
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
PYTHON="${PYTHON:-python3}"

# ---- Load the config (the single edit point) -------------------------------
CONF="${ADOPT_CONF:-$SCRIPT_DIR/adopt.conf}"
if [ ! -f "$CONF" ]; then
  echo "ERROR: adoption config not found: $CONF" >&2
  echo "       create it (copy $SCRIPT_DIR/adopt.conf) and set SERVICE_NAME / DOMAIN / REGISTRY" >&2
  exit 1
fi
# shellcheck source=/dev/null
source "$CONF"

DRY_RUN="false"
for arg in "$@"; do
  [ "$arg" = "--dry-run" ] && DRY_RUN="true"
done

# ---- Validate the config ----------------------------------------------------
if [ -z "${SERVICE_NAME:-}" ] || [ -z "${DOMAIN:-}" ] || [ -z "${REGISTRY:-}" ]; then
  echo "❌ adopt.conf must set SERVICE_NAME, DOMAIN and REGISTRY (none may be empty)" >&2
  exit 1
fi
if ! [[ "${SERVICE_NAME}" =~ ^[a-z][a-z0-9_]*$ ]]; then
  echo "❌ Invalid SERVICE_NAME '${SERVICE_NAME}' (use lowercase letters, digits, underscores; start with a letter)" >&2
  exit 1
fi
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "ERROR: python3 is required to run the adapter (install Python 3.8+)" >&2
  exit 127
fi

# ---- Shared run coordinates (one backup dir for both steps) -----------------
# Backups live with the repo being adapted, so --root (when set) steers them too.
TS="$(date +%Y%m%d-%H%M%S)"
ADAPT_ROOT="${ADAPT_ROOT:-}"
if [ -n "${ADAPT_ROOT}" ]; then
  BACKUP_DIR="${ADAPT_ROOT}/.adapt-backup/${TS}"
else
  BACKUP_DIR=".adapt-backup/${TS}"
fi
[ "${DRY_RUN}" != "true" ] && mkdir -p "${BACKUP_DIR}"

# Reuse the name adapter's file selection so both steps touch the same tree (the file types and
# excluded paths come from _common.py and infra/adapt/excludes.txt, read by both Python scripts).

common_args=(
  --backup-dir   "${BACKUP_DIR}"
)
[ -n "${ADAPT_ROOT}" ] && common_args+=( --root "${ADAPT_ROOT}" )
[[ "${DRY_RUN}" == "true" ]] && common_args+=( --dry-run )

echo "[adopt] SERVICE_NAME=${SERVICE_NAME}  DOMAIN=${DOMAIN}  REGISTRY=${REGISTRY}"
[[ "${DRY_RUN}" == "true" ]] && echo "[adapt] (dry-run — no files will be modified)"
echo

# ---- 1) Identity: the service name -----------------------------------------
echo "==> [1/2] Service name (${SERVICE_NAME})…"
python3 "${SCRIPT_DIR}/replace_service_name.py" --new "${SERVICE_NAME}" "${common_args[@]}"
echo

# ---- 2) Deployment: domain + registry --------------------------------------
echo "==> [2/2] Domain (${DOMAIN}) + registry (${REGISTRY})${REPOSITORY:+ + repository (${REPOSITORY})}…"
stamp_args=( --domain "${DOMAIN}" --registry "${REGISTRY}" )
[ -n "${REPOSITORY:-}" ] && stamp_args+=( --repository "${REPOSITORY}" )
[ -n "${CODEOWNER:-}" ] && stamp_args+=( --codeowner "${CODEOWNER}" )
python3 "${SCRIPT_DIR}/adopt_stamp.py" "${stamp_args[@]}" "${common_args[@]}"
echo

if [ "${DRY_RUN}" == "true" ]; then
  echo "✅ Adoption preview complete. Re-run without --dry-run to apply."
else
  echo "✅ Adoption complete. Backups in: ${BACKUP_DIR}"
  echo "   Next: set the per-environment image tag + create the pull secret, then deploy (wiki/adaptation.md)."
fi
