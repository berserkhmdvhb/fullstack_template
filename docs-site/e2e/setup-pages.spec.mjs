// The five setup guides, loaded from wiki/, render as /setup/ pages: one H1 each (the file's own heading,
// not repeated), their code blocks and a working cross-guide link.
import { expect, test } from '@playwright/test';

import { base } from '../src/lib/repo.mjs';

const root = base.replace(/\/$/, '');
const SLUGS = ['platform', 'gke', 'cloudflare', 'github-pages', 'pr-agent'];

for (const slug of SLUGS) {
  test(`/setup/${slug}/ renders once-titled with code blocks`, async ({ page }) => {
    await page.goto(`${root}/setup/${slug}/`);
    await expect(page.locator('main h1')).toHaveCount(1);
    expect(await page.locator('.expressive-code').count()).toBeGreaterThan(0);
  });
}

test('the sidebar lists the five guides in order', async ({ page }) => {
  await page.goto(`${root}/setup/platform/`);
  const links = await page.locator('nav.sidebar a[href*="/setup/"]').evaluateAll((as) => as.map((a) => a.getAttribute('href')));
  expect(links).toEqual(SLUGS.map((s) => `${root}/setup/${s}/`));
});

test('a link between guides is a site link and its anchor exists', async ({ page }) => {
  await page.goto(`${root}/setup/platform/`);
  const href = `${root}/setup/gke/#24-production-in-its-own-project`;
  await page.locator(`main a[href="${href}"]`).first().click();
  await expect(page.locator('[id="24-production-in-its-own-project"]')).toHaveCount(1);
});
