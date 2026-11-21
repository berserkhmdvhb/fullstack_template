#!/usr/bin/env bash
# Refresh (default) or check (--check, read-only) the pinned checksums against the vendors' published
# ones; see scripts/pins_refresh.py. Usage: pins-refresh.sh [--check] [--base REF] [name...]
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 -I "$ROOT/scripts/pins_refresh.py" "$@"
