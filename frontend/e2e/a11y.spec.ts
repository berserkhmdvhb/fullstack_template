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
import { expect, test, type Page } from '@playwright/test';

import { lowContrastText } from './support/contrast';

const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
// axe cannot sample the background of table cells and row buttons that the items table's horizontal scroll box clips (or the
// open delete dialog covers) on a phone ("partially obscured"). These selectors, and only these, may stay undecided there: the
// measured-contrast backstop below still checks the same text from its computed styles.
const CLIPPED_TABLE_CELLS =
  /^(\.table-row-hover:nth-child\(\d+\) > (\.text-xs|td:nth-child\(3\) > \.badge-status > span:nth-child\(2\))|\.text-left:nth-child\([34]\)|button\[aria-label="(Edit|Delete) [^"]+"\]|thead > tr > \.text-right)$/;

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

type Scenario = {
  name: string;
  path: string;
  health?: 'down';
  items?: 'down';
  /** Reach the state under test; it runs after the page has loaded. */
  arrange: (page: Page) => Promise<void>;
};

const nothing = async (): Promise<void> => {};

// States that depend on the backend or on what the user does are scanned too, not only the first paint.
const SCENARIOS: Scenario[] = [
  {
    name: '/',
    path: '/',
    arrange: async (page) => {
      await expect(page.locator('main')).toContainText('Backend healthy');
    },
  },
  {
    name: '/ (backend down)',
    path: '/',
    health: 'down',
    arrange: async (page) => {
      await expect(page.locator('main')).toContainText('Backend unreachable');
    },
  },
  {
    name: '/items',
    path: '/items',
    arrange: async (page) => {
      await expect(page.locator('td', { hasText: 'First item' })).toBeVisible();
    },
  },
  {
    name: '/items (list failed)',
    path: '/items',
    items: 'down',
    arrange: async (page) => {
      await expect(page.locator('main')).toContainText('Failed to load items.');
    },
  },
  {
    name: '/items (form errors)',
    path: '/items',
    arrange: async (page) => {
      await page.locator('button[type="submit"]').click();
      await expect(page.locator('#name-error')).toBeVisible();
    },
  },
  {
    name: '/items (delete dialog)',
    path: '/items',
    arrange: async (page) => {
      await page.locator('button[aria-label="Delete First item"]').click();
      await expect(page.locator('[role="dialog"]')).toBeVisible();
    },
  },
  {
    name: '/ (skip link focused)',
    path: '/',
    arrange: async (page) => {
      await page.keyboard.press('Tab');
      await expect(page.locator('a[href="#main"]')).toBeFocused();
    },
  },
  { name: '/no-such-page', path: '/no-such-page', arrange: nothing },
];

for (const [size, width] of Object.entries(WIDTHS)) {
  for (const theme of THEMES) {
    for (const { name, path, health, items, arrange } of SCENARIOS) {
      test(`${name} has no axe violation (${theme}, ${size} ${String(width)}px)`, async ({
        page,
      }) => {
        await page.setViewportSize({ width, height: 800 });
        await page.addInitScript((value) => {
          localStorage.setItem('theme', value);
        }, theme);
        await page.route('**/api/v1/health', (route) =>
          health === 'down' ? route.abort() : route.fulfill({ json: { status: 'ok' } }),
        );
        await page.route('**/api/v1/items?*', (route) =>
          items === 'down' ? route.abort() : route.fulfill({ json: ITEMS }),
        );
        await page.goto(path);
        await page.waitForLoadState('networkidle');
        await expect(page.locator('html')).toHaveClass(new RegExp(theme));
        await arrange(page);

        const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();

        // axe files text it cannot resolve under `incomplete`; none of that may hide a contrast bug.
        // Allowlist a node only with its selector and a reason (none is needed today).
        const undecided = results.incomplete
          .filter((v) => v.id === 'color-contrast')
          .flatMap((v) => v.nodes)
          .filter((n) => !(size === 'phone' && CLIPPED_TABLE_CELLS.test(n.target.join(' '))))
          .map((n) => `${n.target.join(' ')}: ${n.failureSummary ?? ''}`);
        expect(undecided).toEqual([]);
        // The backstop: measured contrast of every visible text node, from computed styles.
        expect(await lowContrastText(page)).toEqual([]);

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
