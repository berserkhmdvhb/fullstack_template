// The axe scan of the built site (e2e/a11y.spec.mjs): `astro preview` serves dist/ on its own port.
import { defineConfig } from '@playwright/test';

import { base } from './src/lib/repo.mjs';

const port = process.env.DOCS_SITE_PORT ?? '4399';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: true,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: { baseURL: `http://127.0.0.1:${port}`, browserName: 'chromium' },
  webServer: {
    command: `pnpm exec astro preview --ignore-lock --host 127.0.0.1 --port ${port}`,
    url: `http://127.0.0.1:${port}${base}/`.replace(/\/+$/, '/'),
    reuseExistingServer: false,
  },
});
