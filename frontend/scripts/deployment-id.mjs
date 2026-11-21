// The Next.js deploymentId the image build derives: the version and the first 12 characters of the
// commit, so two builds of the same version still differ (a client from the other build then reloads
// instead of requesting chunks that are gone). Next.js accepts letters, digits, - and _ only. Empty
// when neither value is known (local dev): the id then stays unset.
// Dockerfile.frontend runs: node scripts/deployment-id.mjs "$VERSION" "$VCS_REF"
import { fileURLToPath } from 'node:url';

/**
 * @param {string | undefined} version The image version.
 * @param {string | undefined} commit The commit the image is built from.
 * @returns {string} The deployment id, or an empty string.
 */
export function deploymentId(version, commit) {
  return [version ?? '', (commit ?? '').slice(0, 12)]
    .filter(Boolean)
    .join('-')
    .replace(/[^A-Za-z0-9_-]/g, '_');
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  process.stdout.write(deploymentId(process.argv[2], process.argv[3]));
}
