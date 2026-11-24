// Accessibility gate for the built site: axe scans every page in dist/, in the light and the dark theme, and
// fails on a serious or critical violation (the frontend's tooling: @playwright/test and @axe-core/playwright).
import { readdirSync } from 'node:fs';

import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

import { base } from '../src/lib/repo.mjs';

const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const BLOCKING = new Set(['serious', 'critical']);

const pages = readdirSync(new URL('../dist/', import.meta.url), { recursive: true })
  .filter((f) => f.endsWith('.html'))
  .map((f) => `${base}/${f.replace(/(^|\/)index\.html$/, '$1')}`.replace(/\/{2,}/g, '/'));

test('the build produced pages', () => {
  expect(pages.length).toBeGreaterThan(1);
});

for (const path of pages) {
  for (const colorScheme of ['light', 'dark']) {
    test(`${path} (${colorScheme})`, async ({ page }) => {
      await page.emulateMedia({ colorScheme });
      await page.goto(path);
      // Expressive Code makes a scrolling code block focusable from a script once the page is idle.
      await page.waitForFunction(() =>
        [...document.querySelectorAll('.expressive-code pre')].every(
          (pre) => pre.scrollWidth <= pre.clientWidth || pre.hasAttribute('tabindex'),
        ),
      );
      const { violations } = await new AxeBuilder({ page }).withTags(TAGS).analyze();
      const blocking = violations.filter((v) => BLOCKING.has(v.impact));
      expect(blocking.map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target).join(', ')}`)).toEqual([]);
    });
  }
}
