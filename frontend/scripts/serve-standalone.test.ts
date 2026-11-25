/**
 * Tests for the standalone server preparation.
 *
 * @packageDocumentation
 */

import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { afterEach, describe, expect, it } from 'vitest';

import { prepareStandalone } from './serve-standalone.mjs';

const roots: string[] = [];

/** A fake frontend directory with a build, with or without a `public` directory. */
function fakeBuild({ withPublic }: { withPublic: boolean }): string {
  const root = mkdtempSync(join(tmpdir(), 'standalone-'));
  roots.push(root);
  mkdirSync(join(root, '.next', 'standalone', '.next', 'static'), { recursive: true });
  writeFileSync(join(root, '.next', 'standalone', 'server.js'), '');
  writeFileSync(join(root, '.next', 'standalone', '.next', 'static', 'stale.js'), 'stale');
  mkdirSync(join(root, '.next', 'static', 'chunks'), { recursive: true });
  writeFileSync(join(root, '.next', 'static', 'chunks', 'a.js'), 'chunk');
  if (withPublic) {
    mkdirSync(join(root, 'public'));
    writeFileSync(join(root, 'public', 'robots.txt'), 'x');
  }
  return root;
}

afterEach(() => {
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});

describe('prepareStandalone', () => {
  it('copies the static chunks and public files beside the server', () => {
    const root = fakeBuild({ withPublic: true });

    const server = prepareStandalone(root);

    expect(server).toBe(join(root, '.next', 'standalone', 'server.js'));
    const bundle = join(root, '.next', 'standalone');
    expect(readFileSync(join(bundle, '.next', 'static', 'chunks', 'a.js'), 'utf8')).toBe('chunk');
    expect(existsSync(join(bundle, '.next', 'static', 'stale.js'))).toBe(false);
    expect(existsSync(join(bundle, 'public', 'robots.txt'))).toBe(true);
  });

  it('works without a public directory', () => {
    const root = fakeBuild({ withPublic: false });

    expect(() => prepareStandalone(root)).not.toThrow();
  });

  it('asks for a build when there is none', () => {
    const root = mkdtempSync(join(tmpdir(), 'standalone-'));
    roots.push(root);

    expect(() => prepareStandalone(root)).toThrow(/pnpm run build/);
  });
});
