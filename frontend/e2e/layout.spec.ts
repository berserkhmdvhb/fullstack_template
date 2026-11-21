/**
 * The header must fit: no overflow, the service name on one line, every nav link fully visible.
 *
 * @remarks
 * Guards the phone layout (390 px) that used to wrap the name over three lines and break "API Docs", and
 * the desktop layout (1280 px) that must stay on one row.
 *
 * @packageDocumentation
 */

import { expect, test } from '@playwright/test';

import { mockApi } from './support/mock-api';

for (const [size, width] of [
  ['phone', 390],
  ['desktop', 1280],
] as const) {
  for (const path of ['/', '/items']) {
    test(`the header fits on ${path} (${size} ${String(width)}px)`, async ({ page }) => {
      await page.setViewportSize({ width, height: 800 });
      await mockApi(page);
      await page.goto(path);
      await page.waitForLoadState('networkidle');

      const fit = await page.evaluate(() => {
        const header = document.querySelector('header');
        const nav = document.querySelector('header nav');
        if (!header || !nav) throw new Error('no header or nav');
        const lines = (el: Element): number => {
          const style = getComputedStyle(el);
          const lineHeight =
            Number.parseFloat(style.lineHeight) || Number.parseFloat(style.fontSize) * 1.2;
          return Math.round(el.getBoundingClientRect().height / lineHeight);
        };
        const links = [...nav.querySelectorAll('a')].map((a) => {
          const box = a.getBoundingClientRect();
          return {
            text: a.textContent,
            left: box.left,
            right: box.right,
            lines: lines(a.querySelector('span') ?? a),
          };
        });
        const name = nav.querySelector('a span');
        return {
          viewport: window.innerWidth,
          pageOverflow: document.documentElement.scrollWidth - window.innerWidth,
          headerOverflow: header.scrollWidth - header.clientWidth,
          navOverflow: nav.scrollWidth - nav.clientWidth,
          nameLines: name ? lines(name) : 0,
          links,
        };
      });

      expect(fit.pageOverflow).toBeLessThanOrEqual(0);
      expect(fit.headerOverflow).toBeLessThanOrEqual(0);
      expect(fit.navOverflow).toBeLessThanOrEqual(0);
      expect(fit.nameLines).toBe(1);
      expect(fit.links.map((l) => l.text)).toEqual([
        'Your Service Name',
        'Home',
        'Items',
        'API Docs',
      ]);
      for (const link of fit.links) {
        expect(link.left, link.text ?? '').toBeGreaterThanOrEqual(0);
        expect(link.right, link.text ?? '').toBeLessThanOrEqual(fit.viewport);
        expect(link.lines, link.text ?? '').toBe(1);
      }
    });
  }
}

// The items list: below 768 px each row is a stacked card, so Edit and Delete never hide behind a sideways scroll.
for (const width of [390, 600, 767, 768, 1280]) {
  test(`the items list shows its actions without sideways scroll (${String(width)}px)`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 800 });
    await mockApi(page);
    await page.goto('/items');
    await page.waitForLoadState('networkidle');
    await expect(page.locator('td', { hasText: 'First item' })).toBeVisible();

    const fit = await page.evaluate(() => {
      const box = document.querySelector('.overflow-x-auto');
      if (!box) throw new Error('no list box');
      const buttons = [...document.querySelectorAll('button[aria-label$="First item"]')].map(
        (b) => {
          const r = b.getBoundingClientRect();
          return {
            name: b.getAttribute('aria-label'),
            left: r.left,
            right: r.right,
            height: r.height,
          };
        },
      );
      return {
        viewport: window.innerWidth,
        pageOverflow: document.documentElement.scrollWidth - window.innerWidth,
        listOverflow: box.scrollWidth - box.clientWidth,
        buttons,
      };
    });

    expect(fit.pageOverflow).toBeLessThanOrEqual(0);
    expect(fit.listOverflow).toBeLessThanOrEqual(0);
    expect(fit.buttons.map((b) => b.name)).toEqual(['Edit First item', 'Delete First item']);
    for (const button of fit.buttons) {
      expect(button.left, button.name ?? '').toBeGreaterThanOrEqual(0);
      expect(button.right, button.name ?? '').toBeLessThanOrEqual(fit.viewport);
      expect(button.height, button.name ?? '').toBeGreaterThanOrEqual(24);
    }
  });
}
