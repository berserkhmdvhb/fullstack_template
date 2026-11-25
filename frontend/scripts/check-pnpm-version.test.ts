/**
 * Tests for the packageManager / pnpm version check.
 *
 * @packageDocumentation
 */

import { describe, expect, it } from 'vitest';

import { checkPnpmVersion } from './check-pnpm-version.mjs';

describe('checkPnpmVersion', () => {
  it('accepts the named pnpm', () => {
    expect(checkPnpmVersion('pnpm@12.22.0', '12.22.0\n')).toBeNull();
  });

  it('rejects another pnpm', () => {
    expect(checkPnpmVersion('pnpm@12.22.0', '12.23.0')).toMatch(/12\.23\.0.*pnpm@12\.22\.0/);
  });

  it('rejects a malformed field', () => {
    expect(checkPnpmVersion('pnpm@12', '12.22.0')).toMatch(/must be pnpm@/);
    expect(checkPnpmVersion('pnpm@12.22.0+sha512.abc', '12.22.0')).toMatch(/must be pnpm@/);
    expect(checkPnpmVersion('', '12.22.0')).toMatch(/must be pnpm@/);
  });

  it('rejects another package manager', () => {
    expect(checkPnpmVersion('npm@11.20.0', '11.20.0')).toMatch(/must be pnpm@/);
  });
});
