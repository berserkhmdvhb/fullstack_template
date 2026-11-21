/**
 * Smoke tests for the full stack (via the Next.js frontend).
 *
 * @remarks
 * These tests run against the Next.js frontend (default :3000), which serves the app
 * routes and proxies /api/v1, /docs, /openapi.json to the backend (see
 * frontend/proxy.ts).
 *
 * @packageDocumentation
 */

import { expect, test } from '@playwright/test';

const API_BASE = '/api/v1';

test.describe('Frontend (Next.js)', () => {
  test('should load the home page', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');
    await expect(page).toHaveTitle(/Your Service Name/);
    await expect(page.locator('main')).toBeVisible();
  });

  test('should load static assets (CSS)', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');

    const cssLinkCount = await page.evaluate(() => {
      return document.querySelectorAll('link[rel="stylesheet"]').length;
    });

    expect(cssLinkCount).toBeGreaterThan(0);
  });

  test('should load items page', async ({ page }) => {
    await page.goto('/items');
    await page.waitForLoadState('domcontentloaded');

    await expect(page.locator('h1')).toHaveText('Items');
  });
});

test.describe('Backend API (proxied by Next.js)', () => {
  test('should return health status', async ({ request }) => {
    const response = await request.get(`${API_BASE}/health`);
    expect(response.status()).toBe(200);

    const body = (await response.json()) as { status: string };
    expect(body.status).toBe('ok');
  });

  test('should proxy API docs', async ({ request }) => {
    const response = await request.get('/docs');
    expect(response.status()).toBe(200);
  });

  test('should proxy OpenAPI spec', async ({ request }) => {
    const response = await request.get('/openapi.json');
    expect(response.status()).toBe(200);

    const body = (await response.json()) as { openapi: string; info: { title: string } };
    expect(body.openapi).toMatch(/^3\.\d+\.\d+$/);
    expect(body.info.title).toBeTruthy();
  });

  test('should proxy items endpoint', async ({ request }) => {
    const response = await request.get(`${API_BASE}/items`);
    expect(response.status()).toBe(200);

    const body = (await response.json()) as { items: unknown[]; total: number };
    expect(Array.isArray(body.items)).toBe(true);
    expect(typeof body.total).toBe('number');
  });
});

test.describe('Routing', () => {
  test('should serve the frontend liveness probe (/api/healthz)', async ({ request }) => {
    // Served by the frontend itself (not proxied — it is outside /api/v1).
    const response = await request.get('/api/healthz');
    expect(response.status()).toBe(200);
  });

  test('should route /api/* to backend', async ({ request }) => {
    const response = await request.get(`${API_BASE}/health`);
    expect(response.status()).toBe(200);

    // Verify it's the backend response (JSON, not HTML)
    const contentType = response.headers()['content-type'];
    expect(contentType).toContain('application/json');
  });

  test('should route / to frontend (Next.js)', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');

    // Frontend returns HTML with React content
    const html = await page.content();
    expect(html).toContain('<html');
    expect(html.toLowerCase()).toContain('next');
  });

  test('should serve a real script chunk as immutable JavaScript', async ({ request }) => {
    // Take a chunk the page actually references, so a missing or renamed bundle fails here.
    const html = await (await request.get('/')).text();
    const chunk = /\/_next\/static\/[\w./-]+\.js/.exec(html)?.[0];
    if (chunk === undefined) throw new Error('the page references no static script chunk');

    const response = await request.get(chunk);

    expect(response.status()).toBe(200);
    expect(response.headers()['content-type']).toMatch(/javascript/);
    expect(response.headers()['cache-control']).toBe('public, max-age=31536000, immutable');
  });
});
