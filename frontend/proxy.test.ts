/**
 * Unit tests for the per-request CSP built in proxy.ts: the reporting directives and header
 * appear only when CSP_REPORTING_ENABLED is set (read at request time).
 *
 * @packageDocumentation
 */

import { NextRequest } from 'next/server';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { proxy } from './proxy';
import { API_PATH, CSP_REPORT_PATH } from './static-paths';

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
    expect(policy).toContain(`report-uri ${CSP_REPORT_PATH}`);
    expect(policy).toContain('report-to csp-endpoint');
    expect(response.headers.get('Reporting-Endpoints')).toBe(`csp-endpoint="${CSP_REPORT_PATH}"`);
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
      .replace(`; report-uri ${CSP_REPORT_PATH}; report-to csp-endpoint`, '');
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

  it.each(['/', '/items', '/docsx', `${API_PATH}/items`, '/openapi.json'])(
    'keeps the strict nonce policy, with no CDN, on %s',
    (path) => {
      const policy = csp(path);
      expect(policy).toMatch(/script-src 'self' 'nonce-[^']+';/);
      expect(policy).toContain("style-src 'self';");
      expect(policy).not.toContain('cdn.jsdelivr.net');
      expect(policy).not.toContain('unsafe-');
      // form-action has no default-src fallback (ZAP rule 10055): set it, or any form target is allowed.
      expect(policy).toContain("form-action 'self'");
    },
  );
});

describe('identity headers from the browser', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  const forged = {
    'X-Authenticated-User-Id': 'spoofed-admin',
    'X-Authenticated-User-Roles': 'admin',
    'X-Authenticated-Email': 'spoofed@example.com',
    'X-Trusted-Subject': 'spoofed-admin',
    'X-Trusted-Groups': 'admin',
    Accept: 'application/json',
    'X-Request-Id': 'kept',
  };
  // Next forwards exactly the headers named in x-middleware-override-headers, each as
  // x-middleware-request-<name>; a header missing from that list is dropped.
  const forwarded = (path: string) => {
    // Header names are matched in any letter case, as HTTP does.
    const response = proxy(new NextRequest(`http://localhost${path}`, { headers: forged }));
    const all = new Map([...response.headers.entries()].map(([k, v]) => [k.toLowerCase(), v]));
    const names = (all.get('x-middleware-override-headers') ?? '').toLowerCase().split(',');
    return { names, value: (n: string) => all.get(`x-middleware-request-${n}`) ?? null };
  };

  it.each([`${API_PATH}/items`, '/docs', '/items'])(
    'strips x-authenticated-* and keeps the rest on %s',
    (path) => {
      const { names, value } = forwarded(path);
      expect(names.filter((n) => n.startsWith('x-authenticated-'))).toEqual([]);
      expect(value('x-authenticated-user-id')).toBeNull();
      expect(value('accept')).toBe('application/json');
      expect(value('x-request-id')).toBe('kept');
      expect(names).toContain('x-nonce');
    },
  );

  it('strips the configured identity and roles header names, read per request', () => {
    vi.stubEnv('AUTH_USER_ID_HEADER', 'X-Trusted-Subject');
    vi.stubEnv('AUTH_ROLES_HEADER', ' x-trusted-groups ');
    const { names, value } = forwarded(`${API_PATH}/items`);
    expect(names).not.toContain('x-trusted-subject');
    expect(names).not.toContain('x-trusted-groups');
    expect(value('x-trusted-subject')).toBeNull();
    expect(names.filter((n) => n.startsWith('x-authenticated-'))).toEqual([]);
  });

  it('keeps a custom-named header when it is not configured', () => {
    const { value } = forwarded(`${API_PATH}/items`);
    expect(value('x-trusted-subject')).toBe('spoofed-admin');
  });
});
