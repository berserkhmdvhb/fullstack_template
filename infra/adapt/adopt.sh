#!/usr/bin/env bash
# infra/adapt/adopt.sh
# =============================================================================
# Config-driven template adoption. THIN wrapper over the two Python adapters:
#   * replace_service_name.py -- the service NAME (identity: package, image
#     suffix, k8s name prefix, BACKEND_ENTRY, CI package_name)
#   * adopt_stamp.py          -- the deployment COORDINATES (ingress domain +
#     image registry), and .template-version: the template release this copy
#     was made from (written once; wiki/template-upgrades.md)
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

# ---- Then: the chart's helm-unittest snapshots ------------------------------
# The steps above stamp the names inside them, but a renamed host changes the width of a padded
# column (NOTES' route table), so the snapshots are rendered again from the adopted chart with the
# pinned helm and plugin (scripts/tools.sh install helm4 helm-unittest), when they are installed.
ROOT_DIR="${ADAPT_ROOT:-$PWD}"
CHART_DIR="charts/${SERVICE_NAME//_/-}"
HELM="${ROOT_DIR}/.tools/bin/helm"
if [ "${DRY_RUN}" != "true" ] && [ -d "${ROOT_DIR}/${CHART_DIR}/tests/__snapshot__" ]; then
  if [ -x "${HELM}" ] && [ -d "${ROOT_DIR}/.tools/helm-plugins" ]; then
    echo "==> Chart snapshots (${CHART_DIR})…"
    (cd "${ROOT_DIR}" && HELM_PLUGINS="${ROOT_DIR}/.tools/helm-plugins" "${HELM}" unittest -u "${CHART_DIR}" >/dev/null)
    echo
  else
    echo "[adopt] NOTE: render the chart's snapshots again once the tools are installed:"
    echo "        HELM_PLUGINS=.tools/helm-plugins .tools/bin/helm unittest -u ${CHART_DIR}"
    echo
  fi
fi

# ---- And the Python formatting -----------------------------------------------
# A new name sorts elsewhere among the imports and, of another length, moves where ruff wraps a line,
# so the Python the repo owns has its imports sorted and is formatted again (make ruff-lint-fix
# ruff-format) with the project's ruff: .venv/bin/ruff, or RUFF, when it is installed.
RUFF="${RUFF:-${ROOT_DIR}/.venv/bin/ruff}"
if [ "${DRY_RUN}" != "true" ] && [ -f "${ROOT_DIR}/Makefile" ]; then
  if [ -x "${RUFF}" ]; then
    echo "==> Python imports and formatting (make ruff-lint-fix ruff-format)…"
    (cd "${ROOT_DIR}" && PATH="$(dirname "${RUFF}"):${PATH}" make -s ruff-lint-fix ruff-format)
    echo
  else
    echo "[adopt] NOTE: sort and format the Python again once the backend is installed: make ruff-lint-fix ruff-format"
    echo
  fi
fi

if [ "${DRY_RUN}" == "true" ]; then
  echo "✅ Adoption preview complete. Re-run without --dry-run to apply."
else
  echo "✅ Adoption complete. Backups in: ${BACKUP_DIR}"
  echo "   Next: set the per-environment image tag + create the pull secret, then deploy (wiki/adaptation.md)."
fi
