/**
 * Browser-level guard: no page may violate its own Content-Security-Policy.
 *
 * @remarks
 * A `securitypolicyviolation` event (or a CSP console error) means the policy blocked something the
 * page tried to do, for example `eval`, which the policy forbids without `'unsafe-eval'`.
 *
 * @packageDocumentation
 */

import { expect, test } from '@playwright/test';

const KEY = '__cspViolations';

for (const path of ['/', '/items', '/no-such-page']) {
  test(`${path} reports no CSP violation`, async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on('console', (message) => {
      consoleErrors.push(`${message.type()}: ${message.text()}`);
    });
    // Registered before any page script runs, so no early violation is missed.
    await page.addInitScript((key) => {
      Reflect.set(window, key, []);
      document.addEventListener('securitypolicyviolation', (event) => {
        (Reflect.get(window, key) as string[]).push(
          `${event.violatedDirective} ${event.blockedURI} ${event.sourceFile}:${event.lineNumber} ${event.sample}`,
        );
      });
    }, KEY);

    await page.goto(path);
    await page.waitForLoadState('networkidle');

    expect(await page.evaluate((key) => Reflect.get(window, key) as string[], KEY)).toEqual([]);
    expect(
      consoleErrors.filter((line) => /^error: .*(content security policy|csp)/i.test(line)),
    ).toEqual([]);
  });
}
