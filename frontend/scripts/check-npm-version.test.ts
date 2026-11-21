/**
 * Tests for the packageManager / npm version check.
 *
 * @packageDocumentation
 */

import { describe, expect, it } from 'vitest';

import { checkNpmVersion } from './check-npm-version.mjs';

describe('checkNpmVersion', () => {
  it('accepts the named npm', () => {
    expect(checkNpmVersion('npm@11.20.0', '11.20.0\n')).toBeNull();
  });

  it('rejects another npm', () => {
    expect(checkNpmVersion('npm@11.20.0', '10.9.9')).toMatch(/10\.9\.9.*npm@11\.20\.0/);
  });

  it('rejects a malformed field', () => {
    expect(checkNpmVersion('npm@11', '11.20.0')).toMatch(/must be npm@/);
    expect(checkNpmVersion('npm@11.20.0+sha512.abc', '11.20.0')).toMatch(/must be npm@/);
    expect(checkNpmVersion('', '11.20.0')).toMatch(/must be npm@/);
  });

  it('rejects a non-npm manager', () => {
    expect(checkNpmVersion('pnpm@9.0.0', '9.0.0')).toMatch(/must be npm@/);
  });
});
