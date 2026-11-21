/**
 * Browser-level guard: no page may violate its own Content-Security-Policy.
 *
 * @remarks
 * A `securitypolicyviolation` event (or a CSP console error) means the policy blocked something the
 * page tried to do, for example `eval`, which the policy forbids without `'unsafe-eval'`.
 *
 * @packageDocumentation
 */

import { expect, test, type Page } from '@playwright/test';

const KEY = '__cspViolations';

/** Start collecting violations and console messages before any page script runs. */
async function watch(page: Page): Promise<string[]> {
  const consoleLines: string[] = [];
  page.on('console', (message) => {
    consoleLines.push(`${message.type()}: ${message.text()}`);
  });
  await page.addInitScript((key) => {
    Reflect.set(window, key, []);
    document.addEventListener('securitypolicyviolation', (event) => {
      (Reflect.get(window, key) as string[]).push(
        `${event.violatedDirective} ${event.blockedURI} ${event.sourceFile}:${event.lineNumber} ${event.sample}`,
      );
    });
  }, KEY);
  return consoleLines;
}

/** Fail on any collected `securitypolicyviolation` or CSP console error. */
async function expectNoViolation(page: Page, consoleLines: string[]): Promise<void> {
  expect(await page.evaluate((key) => Reflect.get(window, key) as string[], KEY)).toEqual([]);
  expect(
    consoleLines.filter((line) => /^error: .*(content security policy|csp)/i.test(line)),
  ).toEqual([]);
}

for (const path of ['/', '/items', '/no-such-page']) {
  test(`${path} reports no CSP violation`, async ({ page }) => {
    const consoleLines = await watch(page);
    await page.goto(path);
    await page.waitForLoadState('networkidle');
    await expectNoViolation(page, consoleLines);
  });
}

// The backend's Swagger UI page: its pinned jsDelivr assets (SRI) and its own /docs/init.js must
// load and draw the operations under the page's policy, which has no 'unsafe-inline' for scripts.
test('/docs renders Swagger UI with no CSP violation', async ({ page }) => {
  const consoleLines = await watch(page);
  await page.goto('/docs');
  await expect(page.locator('.swagger-ui .opblock').first()).toBeVisible();
  await expectNoViolation(page, consoleLines);
  // A failed SRI check or a blocked asset shows up as a console error, not always a CSP one.
  expect(consoleLines.filter((line) => line.startsWith('error:'))).toEqual([]);
});
