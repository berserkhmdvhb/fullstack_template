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
import { mockApi } from './support/mock-api';

// axe is deterministic: a violation that shows up only now and then must not hide behind a retry.
test.describe.configure({ retries: 0 });

const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const THEMES = ['light', 'dark'] as const;
const WIDTHS = { desktop: 1280, phone: 390 } as const;

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
        await mockApi(page, {
          health: health === 'down' ? 'down' : 'up',
          items: items === 'down' ? 'down' : 'up',
        });
        await page.goto(path);
        await page.waitForLoadState('networkidle');
        await expect(page.locator('html')).toHaveClass(new RegExp(theme));
        await arrange(page);

        const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();

        // axe files text it cannot resolve under `incomplete`; none of that may hide a contrast bug.
        // A node may be allowlisted only with its selector and a reason in a comment; none is today.
        const undecided = results.incomplete
          .filter((v) => v.id === 'color-contrast')
          .flatMap((v) => v.nodes)
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
