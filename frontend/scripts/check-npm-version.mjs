// Fails when the npm that runs differs from the `packageManager` field of package.json, so CI and the image
// builds run the npm the repository names (the one bundled with the pinned Node, see tools/pins.tsv).
// Run from the frontend directory: node scripts/check-npm-version.mjs
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const FIELD = /^npm@(\d+\.\d+\.\d+)$/;

/**
 * Returns an error message, or null when `actual` is the npm named by `packageManager`.
 * @param {string} packageManager The package.json field, for example npm@11.20.0.
 * @param {string} actual The output of `npm --version`.
 * @returns {string | null}
 */
export function checkNpmVersion(packageManager, actual) {
  const match = FIELD.exec(String(packageManager));
  if (!match) {
    return `packageManager must be npm@<major>.<minor>.<patch>, got ${JSON.stringify(packageManager)}`;
  }
  if (match[1] !== String(actual).trim()) {
    return `npm ${String(actual).trim()} is running but packageManager names npm@${match[1]}; use the npm bundled with the pinned Node`;
  }
  return null;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const { packageManager } = JSON.parse(readFileSync('package.json', 'utf8'));
  const actual = execFileSync('npm', ['--version'], { encoding: 'utf8' });
  const error = checkNpmVersion(packageManager, actual);
  if (error) {
    console.error(`check-npm-version: ${error}`);
    process.exit(1);
  }
}
