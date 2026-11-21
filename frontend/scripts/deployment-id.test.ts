/**
 * The deployment id the image build derives from the version and the commit.
 *
 * @packageDocumentation
 */

import { describe, expect, it } from 'vitest';

import { deploymentId } from './deployment-id.mjs';

describe('deploymentId', () => {
  it('joins the version and the first 12 characters of the commit, sanitised', () => {
    expect(deploymentId('1.2.3', '0123456789abcdef0123456789abcdef01234567')).toBe(
      '1_2_3-0123456789ab',
    );
  });

  it('differs between two builds of the same version', () => {
    expect(deploymentId('0.0.0', 'a'.repeat(40))).not.toBe(deploymentId('0.0.0', 'b'.repeat(40)));
  });

  it('replaces every character Next.js refuses', () => {
    expect(deploymentId('0.1.dev3+g1a2b', 'abc')).toBe('0_1_dev3_g1a2b-abc');
  });

  it('uses what is known, and is unset when nothing is', () => {
    expect(deploymentId('1.2.3', '')).toBe('1_2_3');
    expect(deploymentId('', 'abcdef')).toBe('abcdef');
    expect(deploymentId('', '')).toBe('');
    expect(deploymentId(undefined, undefined)).toBe('');
  });
});
