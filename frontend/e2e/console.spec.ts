/**
 * No page may log a console error, throw, or load a resource that fails, in either theme.
 *
 * @remarks
 * A React hydration mismatch (for example #418) is a console error, so this also guards the theme
 * being decided the same way on the server and in the browser.
 *
 * @packageDocumentation
 */

import { expect, test } from '@playwright/test';

import { mockApi } from './support/mock-api';

for (const theme of ['light', 'dark'] as const) {
  for (const path of ['/', '/items']) {
    test(`${path} logs no console error (${theme})`, async ({ page }) => {
      const problems: string[] = [];
      page.on('console', (message) => {
        if (message.type() === 'error') problems.push(`console: ${message.text()}`);
      });
      page.on('pageerror', (error) => problems.push(`pageerror: ${error.message}`));
      // /docs is the backend's page: nothing may prefetch it, or the frontend proxy calls the backend.
      page.on('request', (request) => {
        if (new URL(request.url()).pathname === '/docs')
          problems.push(`prefetched ${request.url()}`);
      });
      page.on('response', (response) => {
        if (response.status() >= 400)
          problems.push(`${String(response.status())} ${response.url()}`);
      });
      await page.addInitScript((value) => {
        localStorage.setItem('theme', value);
      }, theme);
      await mockApi(page);

      await page.goto(path);
      await page.waitForLoadState('networkidle');
      await expect(page.locator('html')).toHaveClass(new RegExp(theme));

      expect(problems).toEqual([]);
    });
  }
}

// With no stored choice the theme follows the system preference, which is where light mode broke hydration.
for (const scheme of ['light', 'dark'] as const) {
  test(`/ logs no console error with a ${scheme} system preference and no stored theme`, async ({
    browser,
  }) => {
    const context = await browser.newContext({ colorScheme: scheme });
    const page = await context.newPage();
    const problems: string[] = [];
    page.on('console', (message) => {
      if (message.type() === 'error') problems.push(`console: ${message.text()}`);
    });
    page.on('pageerror', (error) => problems.push(`pageerror: ${error.message}`));
    await mockApi(page);

    await page.goto('/');
    await page.waitForLoadState('networkidle');
    await expect(page.locator('html')).toHaveClass(new RegExp(scheme));

    expect(problems).toEqual([]);
    await context.close();
  });
}

test('the theme toggle switches the page and the choice survives a reload', async ({ page }) => {
  await mockApi(page);
  await page.addInitScript(() => {
    if (!sessionStorage.getItem('seeded')) {
      localStorage.setItem('theme', 'dark');
      sessionStorage.setItem('seeded', '1');
    }
  });
  await page.goto('/');
  await expect(page.locator('html')).toHaveClass('dark');

  await page.locator('button[aria-label="Switch to light mode"]').click();
  await expect(page.locator('html')).toHaveClass('light');
  expect(await page.evaluate(() => localStorage.getItem('theme'))).toBe('light');

  await page.reload();
  await expect(page.locator('html')).toHaveClass('light');
  await expect(page.locator('button[aria-label="Switch to dark mode"]')).toBeVisible();
});
