// Serves the production build the way the image does: the standalone server, with `.next/static` and `public`
// copied beside it (Dockerfile.frontend copies the same two directories). Playwright's webServer runs it, so the
// copy steps live here and nowhere else. Run from the frontend directory after `npm run build`:
//   PORT=3100 HOSTNAME=127.0.0.1 node scripts/serve-standalone.mjs
import { spawn } from 'node:child_process';
import { cpSync, existsSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * Copies `.next/static` and `public` into the standalone bundle and returns the path of its `server.js`.
 * @param {string} root The frontend directory.
 * @returns {string} The standalone `server.js`.
 */
export function prepareStandalone(root) {
  const bundle = join(root, '.next', 'standalone');
  const server = join(bundle, 'server.js');
  if (!existsSync(server)) {
    throw new Error(`${server} is missing: run "npm run build" first`);
  }
  // The bundle can hold a symlink to the build host's static directory; replace it with a real copy.
  rmSync(join(bundle, '.next', 'static'), { recursive: true, force: true });
  cpSync(join(root, '.next', 'static'), join(bundle, '.next', 'static'), { recursive: true });
  if (existsSync(join(root, 'public'))) {
    cpSync(join(root, 'public'), join(bundle, 'public'), { recursive: true });
  }
  return server;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const child = spawn(process.execPath, [prepareStandalone(process.cwd())], { stdio: 'inherit' });
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
  child.on('exit', (code) => process.exit(code ?? 1));
}
