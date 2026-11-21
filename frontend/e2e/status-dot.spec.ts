/**
 * The status dot (WCAG 1.4.11, non-text contrast): at least 3:1 against every surface it sits on.
 *
 * @remarks
 * Measured from the rendered colours: the dot's `::before` fill against the backgrounds behind it, for an
 * active and an inactive item, in both themes, on the card, on a hovered row (the table) and on the phone card.
 *
 * @packageDocumentation
 */

import { expect, type Page, test } from '@playwright/test';

import { dotContrast } from './support/contrast';
import { mockApi } from './support/mock-api';

const MIN = 3;

/**
 * Resolve once the dot's row (the card, or the table row) has no finite transition or animation running.
 * `getAnimations` flushes style, so a transition that just started counts; an unrelated animation elsewhere
 * on the page, or one that never ends, cannot hold the spec up.
 */
async function settled(page: Page, selector: string): Promise<void> {
  const dot = page.locator(selector).first();
  await expect
    .poll(() =>
      dot.evaluate((el) => {
        const row = el.closest('tr, li, article, [role="row"]') ?? el.parentElement ?? el;
        return row
          .getAnimations({ subtree: true })
          .filter(
            (a) =>
              a.playState === 'running' && a.effect?.getComputedTiming().iterations !== Infinity,
          ).length;
      }),
    )
    .toBe(0);
}

for (const theme of ['light', 'dark'] as const) {
  for (const [size, width] of [
    ['desktop', 1280],
    ['phone', 390],
  ] as const) {
    test(`status dots keep 3:1 (${theme}, ${size})`, async ({ page }) => {
      await page.setViewportSize({ width, height: 800 });
      await page.addInitScript((value) => {
        localStorage.setItem('theme', value);
      }, theme);
      await mockApi(page);
      await page.goto('/items');
      await page.waitForLoadState('networkidle');
      await expect(page.locator('td', { hasText: 'First item' })).toBeVisible();

      const dots = { active: '.badge-status.active', inactive: '.badge-status.inactive' };
      for (const [status, selector] of Object.entries(dots)) {
        // At rest (the card, or the phone card), then with the pointer over the row (the hover background).
        expect(await dotContrast(page, selector), `${status} at rest`).toBeGreaterThanOrEqual(MIN);
        await page.locator(selector).first().hover();
        // The row's background transitions for 150 ms; measure once it has settled.
        await settled(page, selector);
        expect(await dotContrast(page, selector), `${status} hovered`).toBeGreaterThanOrEqual(MIN);
        await page.mouse.move(0, 0);
        await settled(page, selector);
      }
    });
  }
}
