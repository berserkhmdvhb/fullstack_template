/**
 * Pins the final header set `next.config.ts` adds, per route pattern.
 *
 * @remarks
 * The Content-Security-Policy is per request (`proxy.ts`, pinned in `proxy.test.ts`), and
 * Strict-Transport-Security is set at the edge, so neither appears here.
 *
 * @packageDocumentation
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import nextConfig from './next.config';

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
    expect(await headersFor()).toEqual([
      {
        source: '/:path*',
        count: 5,
        headers: {
          'Referrer-Policy': 'no-referrer',
          'X-Content-Type-Options': 'nosniff',
          'Permissions-Policy': 'geolocation=(), microphone=(), camera=()',
          'Cross-Origin-Opener-Policy': 'same-origin',
          'Cross-Origin-Resource-Policy': 'same-origin',
        },
      },
    ]);
  });

  it('sets no HSTS (the edge owns it) and no CSP (proxy.ts owns it)', async () => {
    vi.stubEnv('NODE_ENV', 'production');
    const [rule] = await headersFor();
    expect(rule?.headers).not.toHaveProperty('Strict-Transport-Security');
    expect(rule?.headers).not.toHaveProperty('Content-Security-Policy');
  });

  it('adds nothing outside production', async () => {
    vi.stubEnv('NODE_ENV', 'development');
    expect(await headersFor()).toEqual([]);
  });

  it('does not advertise the framework', () => {
    expect(nextConfig.poweredByHeader).toBe(false);
  });
});
