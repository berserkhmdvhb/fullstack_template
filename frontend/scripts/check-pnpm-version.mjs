// Fails when the pnpm that runs differs from the `packageManager` field of package.json, so CI and the image
// builds run the pnpm the repository pins (mise.toml; the image's copy is checked against the same sha256).
// pnpm itself does not refuse another version (pnpm-workspace.yaml's `pmOnFail: ignore`, which keeps the lockfile one YAML
// document for GitHub's dependency graph), so this check is what enforces it: every CI job and the image build run it
// before they install (tests/supply_chain/test_pnpm_config.py).
// Run from the frontend or the docs-site directory: node scripts/check-pnpm-version.mjs
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const FIELD = /^pnpm@(\d+\.\d+\.\d+)$/;

/**
 * Returns an error message, or null when `actual` is the pnpm named by `packageManager`.
 * @param {string} packageManager The package.json field, for example pnpm@12.22.0.
 * @param {string} actual The output of `pnpm --version`.
 * @returns {string | null}
 */
export function checkPnpmVersion(packageManager, actual) {
  const match = FIELD.exec(String(packageManager));
  if (!match) {
    return `packageManager must be pnpm@<major>.<minor>.<patch>, got ${JSON.stringify(packageManager)}`;
  }
  if (match[1] !== String(actual).trim()) {
    return `pnpm ${String(actual).trim()} is running but packageManager names pnpm@${match[1]}; run scripts/tools.sh install pnpm`;
  }
  return null;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const { packageManager } = JSON.parse(readFileSync('package.json', 'utf8'));
  let actual = '';
  try {
    actual = execFileSync('pnpm', ['--version'], { encoding: 'utf8' });
  } catch {
    actual = '(none on PATH)';
  }
  const error = checkPnpmVersion(packageManager, actual);
  if (error) {
    console.error(`check-pnpm-version: ${error}`);
    process.exit(1);
  }
}
