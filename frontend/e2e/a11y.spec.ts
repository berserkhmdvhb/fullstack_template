/**
 * Accessibility gate: axe scans every app page in both themes at desktop and phone widths.
 *
 * @remarks
 * Zero violations are allowed for WCAG 2.0/2.1 A and AA plus 2.2 AA. No rule is disabled. `/docs` is
 * the backend's Swagger UI (a third-party page), so it is not scanned here. The items API is
 * answered by a fixture, so the page renders the same list with or without a backend.
 *
 * @packageDocumentation
 */

import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
// The home page shows a status that depends on the backend, so both states are scanned.
const PAGES = [
  { name: '/', path: '/', health: 'up' },
  { name: '/ (backend down)', path: '/', health: 'down' },
  { name: '/items', path: '/items', health: 'up' },
  { name: '/no-such-page', path: '/no-such-page', health: 'up' },
] as const;
const THEMES = ['light', 'dark'] as const;
const WIDTHS = { desktop: 1280, phone: 390 } as const;

const ITEMS = {
  items: [
    {
      id: '11111111-1111-1111-1111-111111111111',
      name: 'First item',
      description: 'A described item',
      is_active: true,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    },
    {
      id: '22222222-2222-2222-2222-222222222222',
      name: 'Second item',
      description: null,
      is_active: false,
      created_at: '2026-01-02T00:00:00Z',
      updated_at: '2026-01-02T00:00:00Z',
    },
  ],
  total: 2,
  page: 1,
  page_size: 20,
  total_pages: 1,
};

for (const [size, width] of Object.entries(WIDTHS)) {
  for (const theme of THEMES) {
    for (const { name, path, health } of PAGES) {
      test(`${name} has no axe violation (${theme}, ${size} ${String(width)}px)`, async ({
        page,
      }) => {
        await page.setViewportSize({ width, height: 800 });
        await page.addInitScript((value) => {
          localStorage.setItem('theme', value);
        }, theme);
        await page.route('**/api/v1/health', (route) =>
          health === 'up' ? route.fulfill({ json: { status: 'ok' } }) : route.abort(),
        );
        await page.route('**/api/v1/items?*', (route) => route.fulfill({ json: ITEMS }));
        await page.goto(path);
        await page.waitForLoadState('networkidle');
        await expect(page.locator('html')).toHaveClass(new RegExp(theme));
        if (path === '/') {
          await expect(page.locator('main')).toContainText(
            health === 'up' ? 'Backend healthy' : 'Backend unreachable',
          );
        }
        if (path === '/items')
          await expect(page.locator('td', { hasText: 'First item' })).toBeVisible();

        const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();

        // A readable summary: the rule, the elements and axe's own explanation.
        expect(
          results.violations.map((v) => ({
            rule: v.id,
            nodes: v.nodes.map((n) => `${n.target.join(' ')}: ${n.failureSummary ?? ''}`),
          })),
        ).toEqual([]);
      });
    }
  }
}
