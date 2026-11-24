/**
 * Pins the final header set `next.config.ts` adds, per route pattern.
 *
 * @remarks
 * The Content-Security-Policy is per request (`proxy.ts`, pinned in `proxy.test.ts`), and
 * Strict-Transport-Security comes from the edge or the backend, so neither appears here.
 *
 * @packageDocumentation
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import nextConfig from './next.config';
import { STATIC_HEADER_SOURCES } from './static-paths';

const headersFor = async () => {
  const rules = (await nextConfig.headers?.()) ?? [];
  return rules.map((rule) => ({
    source: rule.source,
    headers: Object.fromEntries(rule.headers.map((h) => [h.key, h.value])),
    count: rule.headers.length,
  }));
};

describe('next.config headers()', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('adds exactly this set to every route in production', async () => {
    vi.stubEnv('NODE_ENV', 'production');
    const [everyRoute] = await headersFor();
    expect(everyRoute).toEqual({
      source: '/:path*',
      count: 6,
      headers: {
        'Referrer-Policy': 'no-referrer',
        'X-Content-Type-Options': 'nosniff',
        'X-Frame-Options': 'DENY',
        'Permissions-Policy': 'geolocation=(), microphone=(), camera=()',
        'Cross-Origin-Opener-Policy': 'same-origin',
        'Cross-Origin-Resource-Policy': 'same-origin',
      },
    });
  });

  it('sets the cache policy per file', async () => {
    vi.stubEnv('NODE_ENV', 'production');
    const cacheRules = (await headersFor()).slice(5);
    expect(cacheRules).toEqual([
      { source: '/logo.png', count: 1, headers: { 'Cache-Control': 'public, max-age=604800' } },
      { source: '/favicon.ico', count: 1, headers: { 'Cache-Control': 'public, max-age=604800' } },
      { source: '/robots.txt', count: 1, headers: { 'Cache-Control': 'no-cache' } },
      { source: '/sitemap.xml', count: 1, headers: { 'Cache-Control': 'no-cache' } },
      { source: '/health.txt', count: 1, headers: { 'Cache-Control': 'no-cache' } },
    ]);
  });

  it('gives every file the proxy skips a policy that lets nothing run (the proxy sets the page one)', async () => {
    vi.stubEnv('NODE_ENV', 'production');
    const rules = await headersFor();
    expect(rules.slice(1, 5).map((r) => r.source)).toEqual(STATIC_HEADER_SOURCES);
    for (const rule of rules.slice(1, 5)) {
      expect(rule.headers).toEqual({ 'Content-Security-Policy': "default-src 'none'" });
    }
  });

  it('sets no HSTS (the edge or the backend owns it) and no CSP (proxy.ts owns it)', async () => {
    vi.stubEnv('NODE_ENV', 'production');
    const [rule] = await headersFor();
    expect(rule?.headers).not.toHaveProperty('Strict-Transport-Security');
    expect(rule?.headers).not.toHaveProperty('Content-Security-Policy');
  });

  it('adds nothing outside production', async () => {
    vi.stubEnv('NODE_ENV', 'development');
    expect(await headersFor()).toEqual([]);
  });

  it('sets the deployment id from NEXT_DEPLOYMENT_ID, and leaves it unset without one', async () => {
    vi.stubEnv('NEXT_DEPLOYMENT_ID', '1.2.3');
    vi.resetModules();
    expect((await import('./next.config')).default.deploymentId).toBe('1.2.3');
    vi.stubEnv('NEXT_DEPLOYMENT_ID', '');
    vi.resetModules();
    expect((await import('./next.config')).default.deploymentId).toBeUndefined();
  });

  it('does not advertise the framework', () => {
    expect(nextConfig.poweredByHeader).toBe(false);
  });
});
