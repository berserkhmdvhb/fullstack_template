/**
 * The one list of static paths: what the proxy skips and what the static header rule covers.
 *
 * @packageDocumentation
 */

import type { ProxyMatcher } from 'next/dist/build/analysis/get-page-static-info';
import * as pageStaticInfo from 'next/dist/build/analysis/get-page-static-info';
import type { BaseNextRequest } from 'next/dist/server/base-http';
import { buildCustomRoute } from 'next/dist/server/lib/router-utils/filesystem';
import { getMiddlewareRouteMatcher } from 'next/dist/shared/lib/router/utils/middleware-route-matcher';
import { describe, expect, it } from 'vitest';

import { config } from './proxy';
import {
  BACKEND_PATHS,
  DIRECTORIES,
  EXTENSIONS,
  FILES,
  proxyMatcher,
  STATIC_HEADER_SOURCES,
} from './static-paths';

// Compiled with Next's own functions, as the server does: the matcher as the build compiles
// `config.matcher` and the runtime tests it (case-sensitive), each header source as the router
// compiles `headers()` (path-to-regexp, case-insensitive unless `caseSensitiveRoutes` is set).
// `getMiddlewareMatchers` (what the build runs on `config.matcher`) is exported but not typed.
const { getMiddlewareMatchers } = pageStaticInfo as unknown as {
  getMiddlewareMatchers: (matcher: string[], nextConfig: object) => ProxyMatcher[];
};
const routeMatcher = getMiddlewareRouteMatcher(getMiddlewareMatchers(config.matcher, {}));
// The request is only read for `has`/`missing` conditions, which the matcher has none of.
const proxyRuns = (path: string) => routeMatcher(path, {} as BaseNextRequest, {});
// `buildCustomRoute` is the function the server's router runs on `headers()` (no base path, and
// `caseSensitiveRoutes` is off), so the header side cannot drift from Next's own router.
const headerSources = STATIC_HEADER_SOURCES.map(
  (source) => buildCustomRoute('header', { source }).match,
);
const staticHeaderApplies = (path: string) => headerSources.some((match) => match(path) !== false);

const mixed = (word: string) =>
  [...word].map((c, i) => (i % 2 === 0 ? c.toUpperCase() : c.toLowerCase())).join('');

/**
 * Every letter case of a literal the matcher must agree on: lower, upper, mixed, and each
 * single-letter flip of the lower and the upper form.
 * @param word The literal.
 * @returns The case variants, without duplicates.
 */
const caseVariants = (word: string): string[] => {
  const lower = word.toLowerCase();
  const upper = word.toUpperCase();
  const flip = (base: string, i: number) =>
    base.slice(0, i) +
    (base[i] === base[i]!.toLowerCase() ? base[i]!.toUpperCase() : base[i]!.toLowerCase()) +
    base.slice(i + 1);
  const flips = [...word].flatMap((_, i) => [flip(lower, i), flip(upper, i)]);
  return [...new Set([lower, upper, mixed(word), ...flips])];
};

const literals = [
  ...BACKEND_PATHS.map((p) => p.slice(1)),
  ...DIRECTORIES,
  ...FILES,
  ...BACKEND_PATHS.map((p) => `${p.slice(1)}x`),
  'api.svg',
  'docs.png',
  'openapi.json.svg',
  '_next/staticfoo',
  '_next/image-x',
  'favicon.icox',
  'a/b',
  `${BACKEND_PATHS[0]!.slice(1)}/a/b`,
  `${DIRECTORIES[0]}/chunks`,
];
const prefixes = ['', ...literals.flatMap(caseVariants)];
const stems = ['', '..', 'photo', 'a.b.c', 'favicon'];
const extensions = [
  '',
  ...[...EXTENSIONS, 'ico'].flatMap(caseVariants).map((e) => `.${e}`),
  ...['json', 'webmanifest', 'html', 'txt', 'xml'].map((e) => `.${e}`),
];
const tails = ['', '/tail'];

/**
 * The cross product of prefix x case x stem x extension x trailing segment.
 * @returns The distinct paths.
 */
const generatedPaths = (): string[] => {
  const paths = new Set<string>();
  for (const prefix of prefixes)
    for (const stem of stems)
      for (const ext of extensions)
        for (const tail of tails)
          paths.add(`/${[prefix, stem + ext].filter(Boolean).join('/')}${tail}`);
  return [...paths];
};

describe('static paths', () => {
  it('the proxy matcher (a literal, as Next.js requires) equals the one built from the list', () => {
    expect(config.matcher).toEqual([proxyMatcher()]);
  });

  it('every path is either static (headers() only) or proxied (the proxy only), never both or neither', () => {
    const paths = generatedPaths();
    const wrong = paths.flatMap((path) => {
      const proxied = proxyRuns(path);
      const isStatic = staticHeaderApplies(path);
      if (proxied !== isStatic) return [];
      return [`${path}: ${proxied ? 'both policies' : 'no policy'}`];
    });
    expect(paths.length).toBeGreaterThan(8000);
    // Known paths, so the check cannot pass when both sides match everything or nothing.
    expect(proxyRuns('/')).toBe(true);
    expect(staticHeaderApplies('/')).toBe(false);
    expect(proxyRuns('/t.svg')).toBe(false);
    expect(staticHeaderApplies('/t.svg')).toBe(true);
    expect(proxyRuns('/api/v1/x')).toBe(true);
    expect(staticHeaderApplies('/api/v1/x')).toBe(false);
    expect(proxyRuns('/_next/static/x.js')).toBe(false);
    expect(staticHeaderApplies('/_next/static/x.js')).toBe(true);
    expect(paths.filter(proxyRuns).length).toBeGreaterThan(0);
    expect(paths.filter(staticHeaderApplies).length).toBeGreaterThan(0);
    const listed = wrong.slice(0, 40).join('\n');
    expect(wrong.length, `${wrong.length} of ${paths.length} paths, the first 40:\n${listed}`).toBe(
      0,
    );
  });
});
