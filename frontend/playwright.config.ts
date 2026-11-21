/**
 * Playwright E2E test configuration.
 *
 * @remarks
 * - Base URL: the Next.js frontend (default http://localhost:3000), which serves the app
 * routes and proxies /api, /docs, /openapi.json to the backend.
 * - Test directory: e2e/
 * - Reporter: list + html
 * - Timeout: 30s per test
 * - `webServer`: only with `E2E_START_SERVER=1` (see below)
 * - One worker (serial execution for reliability)
 *
 * Usage:
 * ```bash
 * npm run e2e          # Run all E2E tests
 * npm run e2e:ui       # Run with Playwright UI mode
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

const port = resolveFrontendPort();

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: !!process.env['CI'],
  retries: process.env['CI'] ? 2 : 0,
  workers: 1,
  reporter: [['list'], ['html', { open: 'never' }]],
  timeout: 30_000,
  use: {
    // Target the Next.js frontend — the single entry point. It serves the app routes
    // and proxies /api, /docs, /openapi.json to the backend (see
    // frontend/proxy.ts). Port follows the running stack (see resolveFrontendPort).
    baseURL: `http://localhost:${port}`,
    // Traces keep request headers, so they are only recorded on a retry after a failure. The CI
    // upload (failure-only) is safe because the stack under test is hermetic: localhost, no real
    // credentials, and the job holds no secrets. An adopter who adds real auth to the live stack
    // must set `trace: 'off'` (traces keep request headers).
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  // Set `E2E_START_SERVER=1` (the `test:a11y` script does) to serve the built app with `next start`, so a
  // spec that needs no backend runs on its own. Without it the suite targets a stack that is already up.
  ...(process.env['E2E_START_SERVER']
    ? {
        webServer: {
          command: `npx next start -H 127.0.0.1 -p ${port}`,
          url: `http://127.0.0.1:${port}/api/healthz`,
          reuseExistingServer: false,
          timeout: 60_000,
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
