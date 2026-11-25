/**
 * Playwright E2E test configuration.
 *
 * @remarks
 * - Base URL: the Next.js frontend (default http://localhost:3000), which serves the app
 * routes and proxies /api, /docs, /openapi.json to the backend.
 * - Test directory: e2e/
 * - Reporter: list + html
 * - Timeout: 30s per test
 * - `webServer`: only with `E2E_START_SERVER=1` (the standalone server, on its own port)
 * - One worker (serial execution for reliability)
 *
 * Usage:
 * ```bash
 * pnpm run e2e          # Run all E2E tests
 * pnpm run e2e:ui       # Run with Playwright UI mode
 * ```
 *
 * @packageDocumentation
 */

import { readFileSync } from 'fs';
import { join } from 'path';
import { defineConfig, devices } from '@playwright/test';

/**
 * Resolve the frontend port the E2E suite should target, in priority order:
 * first, an explicit `FRONTEND_PORT` env var (CI / custom runs) — always wins;
 * then, the repo-root `.env` that `docker compose` also reads, so the suite just
 * works against the local stack with no manual env export; and finally the
 * historical default of 3000 as a last resort.
 */
function resolveFrontendPort(): string {
  if (process.env['FRONTEND_PORT']) {
    return process.env['FRONTEND_PORT'];
  }
  try {
    const envPath = join(__dirname, '..', '.env');
    const match = readFileSync(envPath, 'utf8').match(/^\s*FRONTEND_PORT\s*=\s*(\S+)\s*$/m);
    if (match) {
      // `(\S+)` always captures one token when the overall pattern matches, so
      // group 1 is defined here; the assertion documents that invariant.
      return match[1]!;
    }
  } catch {
    // No repo-root .env available — fall back to the default below.
  }
  return '3000';
}

// `E2E_START_SERVER=1` (the `test:a11y` script sets it) serves the built app with the standalone server the image
// ships (scripts/serve-standalone.mjs), on a port of its own so a stack running locally never collides with it.
// Without it the suite targets a stack that is already up. One host form, 127.0.0.1, in both modes.
const ownServer = Boolean(process.env['E2E_START_SERVER']);
const port = ownServer ? (process.env['E2E_SERVER_PORT'] ?? '3100') : resolveFrontendPort();
const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: !!process.env['CI'],
  retries: process.env['CI'] ? 2 : 0,
  workers: 1,
  reporter: [['list'], ['html', { open: 'never' }]],
  timeout: 30_000,
  use: {
    // Target the Next.js frontend, the single entry point. It serves the app routes and proxies /api, /docs,
    // /openapi.json to the backend (see frontend/proxy.ts). The port follows the running stack (see
    // resolveFrontendPort).
    baseURL,
    // Traces keep request headers, so they are only recorded on a retry after a failure. The CI
    // upload (failure-only) is safe because the stack under test is hermetic: localhost, no real
    // credentials, and the job holds no secrets. An adopter who adds real auth to the live stack
    // must set `trace: 'off'` (traces keep request headers).
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  ...(ownServer
    ? {
        webServer: {
          command: 'node scripts/serve-standalone.mjs',
          url: `${baseURL}/api/healthz`,
          reuseExistingServer: false,
          timeout: 60_000,
          // The proxy's backend is unreachable on purpose: this mode needs none, and no local one may leak in.
          env: { PORT: port, HOSTNAME: '127.0.0.1', BACKEND_URL: 'http://127.0.0.1:1' },
        },
      }
    : {}),
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
