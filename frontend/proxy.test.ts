/**
 * Unit tests for the per-request CSP built in proxy.ts: the reporting directives and header
 * appear only when CSP_REPORTING_ENABLED is set (read at request time).
 *
 * @packageDocumentation
 */

import { NextRequest } from 'next/server';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { proxy } from './proxy';

const run = (path = '/') => proxy(new NextRequest(`http://localhost${path}`));
const csp = (path = '/') => run(path).headers.get('Content-Security-Policy') ?? '';

describe('CSP reporting', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('adds nothing when CSP_REPORTING_ENABLED is unset', () => {
    vi.stubEnv('CSP_REPORTING_ENABLED', undefined);
    const response = run();
    expect(response.headers.get('Content-Security-Policy')).not.toContain('report-');
    expect(response.headers.get('Reporting-Endpoints')).toBeNull();
  });

  it.each(['', 'false', '0', 'no'])('adds nothing for CSP_REPORTING_ENABLED=%j', (value) => {
    vi.stubEnv('CSP_REPORTING_ENABLED', value);
    expect(csp()).not.toContain('report-');
    expect(run().headers.get('Reporting-Endpoints')).toBeNull();
  });

  it.each(['true', 'TRUE', ' true '])('adds the directives and header for %j', (value) => {
    vi.stubEnv('CSP_REPORTING_ENABLED', value);
    const response = run();
    const policy = response.headers.get('Content-Security-Policy') ?? '';
    expect(policy).toContain('report-uri /api/v1/csp-report');
    expect(policy).toContain('report-to csp-endpoint');
    expect(response.headers.get('Reporting-Endpoints')).toBe('csp-endpoint="/api/v1/csp-report"');
  });

  it('is read per request, not when the module loads', () => {
    vi.stubEnv('CSP_REPORTING_ENABLED', undefined);
    expect(csp()).not.toContain('report-uri');
    vi.stubEnv('CSP_REPORTING_ENABLED', 'true');
    expect(csp()).toContain('report-uri');
  });

  it('keeps the rest of the policy unchanged when on', () => {
    vi.stubEnv('CSP_REPORTING_ENABLED', undefined);
    const off = csp().replace(/nonce-[^']+/, 'nonce-X');
    vi.stubEnv('CSP_REPORTING_ENABLED', 'true');
    const on = csp()
      .replace(/nonce-[^']+/, 'nonce-X')
      .replace('; report-uri /api/v1/csp-report; report-to csp-endpoint', '');
    expect(on).toBe(off);
  });
});

describe('the docs page', () => {
  it.each(['/docs', '/docs/init.js'])('%s is proxied and the backend owns its CSP', (path) => {
    const response = run(path);
    expect(response.headers.get('x-middleware-rewrite')).toContain(path);
    expect(response.headers.get('Content-Security-Policy')).toBeNull();
  });

  it.each(['/docs', '/docs/init.js'])('%s gets no reporting header either', (path) => {
    vi.stubEnv('CSP_REPORTING_ENABLED', 'true');
    expect(run(path).headers.get('Reporting-Endpoints')).toBeNull();
    vi.unstubAllEnvs();
  });

  it.each(['/', '/items', '/docsx', '/api/v1/items', '/openapi.json'])(
    'keeps the strict nonce policy, with no CDN, on %s',
    (path) => {
      const policy = csp(path);
      expect(policy).toMatch(/script-src 'self' 'nonce-[^']+';/);
      expect(policy).toContain("style-src 'self';");
      expect(policy).not.toContain('cdn.jsdelivr.net');
      expect(policy).not.toContain('unsafe-');
    },
  );
});
