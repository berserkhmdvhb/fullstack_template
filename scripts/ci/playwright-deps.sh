#!/usr/bin/env bash
# playwright-deps.sh — install Playwright's Chromium system dependencies on a CI runner, surviving a slow apt mirror.
#
# Playwright runs apt-get through sudo, which gives it its own session: when retry.sh's timeout fires, the signal
# never reaches apt-get, so the orphan keeps the dpkg lock and the next attempt fails on it. So this script (1) makes
# apt wait for the lock and bound its own downloads, and (2) gives each attempt a limit that fits a slow mirror, so the
# limit only guards a real hang. Run it from the directory whose Playwright is installed.
#
# Usage: playwright-deps.sh      (CI only: it writes an apt drop-in)
set -euo pipefail

ATTEMPTS=2
DELAY=30
LIMIT=600

[[ "${GITHUB_ACTIONS:-}" == "true" ]] || { echo "playwright-deps.sh: runs on a GitHub Actions runner only; it changes apt's configuration" >&2; exit 1; }

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
conf_dir="${APT_CONF_DIR:-/etc/apt/apt.conf.d}"

# Rewriting the same file is harmless.
sudo tee "${conf_dir}/80-ci-network" > /dev/null <<'CONF'
// A retry waits for the previous attempt's apt-get (it may outlive its timeout) instead of failing on the dpkg lock.
DPkg::Lock::Timeout "600";
// A mirror that drops a download gets retried by apt itself.
Acquire::Retries "5";
// A stalled connection fails fast, so apt's retries run before the attempt's limit.
Acquire::http::Timeout "30";
Acquire::https::Timeout "30";
CONF

exec bash "${here}/retry.sh" "${ATTEMPTS}" "${DELAY}" "${LIMIT}" pnpm exec playwright install-deps chromium
