/**
 * Browser CSP reports reach the backend: a real violation in a real browser, reported to the
 * endpoint the page's own policy names (cspReporting.enabled on the release).
 *
 * @remarks
 * Runs only with `CSP_REPORT_MARKER` set (tests/k8s/test_csp_report.py sets it, through
 * `make kind-csp-report-test`; that test then finds the marker in the backend's log). The test injects
 * an inline script without the page's nonce, which the policy blocks; nothing in the product changes.
 *
 * The policy names the endpoint twice: `report-to` (the Reporting API) and `report-uri`. Chromium uses
 * `report-to` when both are there, but under Playwright it queued those reports and never delivered
 * them (two minutes, Pending then Queued again, also to a plain local server), so this test drops
 * `report-to` and `Reporting-Endpoints` from the page's response in the browser, after checking that
 * the release sent them, and the violation goes out through `report-uri`. The `report-to` delivery is
 * the browser's and is not proven here.
 *
 * @packageDocumentation
 */

import { expect, test } from '@playwright/test';

const marker = process.env['CSP_REPORT_MARKER'];
const REPORT_PATH = '/api/v1/csp-report';

test.skip(!marker, 'CSP_REPORT_MARKER is unset (make kind-csp-report-test sets it)');

test('a blocked inline script is reported to the backend', async ({ page }) => {
  // The marker is in the path, which the backend logs (document-uri; the query string is dropped).
  const path = `/csp-report-check-${marker}`;
  let sent: Record<string, string> = {};
  await page.route(`**${path}`, async (route) => {
    const response = await route.fetch();
    sent = response.headers();
    const headers = { ...sent };
    headers['content-security-policy'] = (sent['content-security-policy'] ?? '')
      .split(';')
      .filter((directive) => !directive.trim().startsWith('report-to '))
      .join(';');
    delete headers['reporting-endpoints'];
    await route.fulfill({ response, headers });
  });
  await page.goto(path);
  expect(sent['content-security-policy']).toContain(`report-uri ${REPORT_PATH}`);
  expect(sent['content-security-policy']).toContain('report-to csp-endpoint');
  expect(sent['reporting-endpoints']).toBe(`csp-endpoint="${REPORT_PATH}"`);

  const report = page.waitForRequest((request) => request.url().endsWith(REPORT_PATH));
  const violation = page.evaluate(
    () =>
      new Promise<string>((resolve) => {
        document.addEventListener('securitypolicyviolation', (event) => {
          resolve(event.effectiveDirective);
        });
        const script = document.createElement('script');
        script.textContent = 'window.__cspReportCheck = true;';
        document.head.append(script);
      }),
  );
  expect(await violation).toMatch(/^script-src/);
  expect(await page.evaluate(() => Reflect.get(window, '__cspReportCheck'))).toBeUndefined();
  const response = await (await report).response();
  expect(response?.status()).toBe(204);
});
