#!/usr/bin/env bash
# your-service-name — adaptable service template (see wiki/getting-started.md)
# =============================================================================
# Template Adaptation: Replace Service Name
# =============================================================================
# Renames the package directory and updates references of
# "your_service_name" -> <NEW_NAME> across source, tests, scripts, CI,
# container image configuration, security configuration, pyproject, etc.

#
# Dry-run: shows planned changes without modifying files.
# Backups: .adapt-backup/<timestamp>/ before any write.
#
# Usage:
#   infra/adapt/replace_service_name.sh --new-name awesome_solution [--old-name your_service_name]
#   infra/adapt/replace_service_name.sh -n awesome_solution --dry-run
#
# NEW_NAME must be a valid Python package identifier: [a-z][a-z0-9_]* (lowercase).
# OLD_NAME is auto-detected from src/* or pyproject.toml if omitted.
# Template Adaptation: Replace Service Name
# =============================================================================
# If not running under bash (e.g., /bin/sh), re-exec with bash.
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi
set -euo pipefail

NEW_NAME=""
OLD_NAME="${OLD_NAME:-}"
DRY_RUN="${DRY_RUN:-false}"
ADAPT_ROOT="${ADAPT_ROOT:-}"
INCLUDE_EXTS="${INCLUDE_EXTS:-.py,.sh,.yml,.yaml,.toml,.cfg,.ini,.env,.def}"
EXCLUDE_DIRS="${EXCLUDE_DIRS:-.git,.venv,venv,__pycache__,.mypy_cache,.ruff_cache,.pytest_cache,infra/adapt,.adapt-backup,wiki}"

print_usage() {
  cat <<'EOF'
Usage: infra/adapt/replace_service_name.sh [options]
  -n, --new-name NAME   New package name (lowercase, [a-z][a-z0-9_]*)
  -o, --old-name NAME   Old package name (default: auto-detect)
  -r, --root PATH       Repo root (default: the current directory)
      --dry-run         Show planned changes only
  -h, --help            Show help
Examples:
  ./replace_service_name.sh -n awesome_solution
  ./replace_service_name.sh -n awesome_solution --dry-run
EOF
}

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    -n|--new-name) NEW_NAME="$2"; shift 2 ;;
    -o|--old-name) OLD_NAME="$2"; shift 2 ;;
    -r|--root)     ADAPT_ROOT="$2"; shift 2 ;;
    --dry-run)     DRY_RUN="true"; shift 1 ;;
    -h|--help)     print_usage; exit 0 ;;
    *)             echo "Unknown option: $1" >&2; print_usage; exit 1 ;;
  esac
done

# Validate inputs
if [[ -z "${NEW_NAME}" ]]; then
  echo "❌ Missing --new-name" >&2
  exit 1
fi
if ! [[ "${NEW_NAME}" =~ ^[a-z][a-z0-9_]*$ ]]; then
  echo "❌ Invalid --new-name '${NEW_NAME}' (use lowercase letters, digits, underscores; start with a letter)" >&2
  exit 1
fi

# Backups directory (created lazily on a real run; the Python backs files into
# it, and a dry-run writes nothing, so don't leave an empty timestamped dir).
# When --root points at another tree, the backups go under that root, not the
# caller's CWD -- they belong with the repo being adapted.
TS="$(date +%Y%m%d-%H%M%S)"
if [ -n "${ADAPT_ROOT}" ]; then
  BACKUP_DIR="${ADAPT_ROOT}/.adapt-backup/${TS}"
else
  BACKUP_DIR=".adapt-backup/${TS}"
fi
[ "${DRY_RUN}" != "true" ] && mkdir -p "${BACKUP_DIR}"

# Resolve script paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${SCRIPT_DIR}/replace_service_name.py"   # your Ruff-clean Python

# Build args array and append conditionally
args=(
  --new          "${NEW_NAME}"
  --old          "${OLD_NAME}"
  --include-exts "${INCLUDE_EXTS}"
  --exclude-dirs "${EXCLUDE_DIRS}"
  --backup-dir   "${BACKUP_DIR}"
)
[ -n "${ADAPT_ROOT}" ] && args+=( --root "${ADAPT_ROOT}" )
[[ "${DRY_RUN}" == "true" ]] && args+=( --dry-run )

# Run Python
python3 "${PY}" "${args[@]}"

echo
if [[ "${DRY_RUN}" == "true" ]]; then
  echo "✅ Service name adaptation completed. (dry-run: no files were modified)"
else
  echo "✅ Service name adaptation completed. Backups in: ${BACKUP_DIR}"
fi
