/**
 * The one list of static paths: what the proxy skips and what the static header rule covers.
 *
 * @packageDocumentation
 */

import { describe, expect, it } from 'vitest';

import { config } from './proxy';
import { proxyMatcher } from './static-paths';

describe('static paths', () => {
  it('the proxy matcher (a literal, as Next.js requires) equals the one built from the list', () => {
    expect(config.matcher).toEqual([proxyMatcher()]);
  });
});
