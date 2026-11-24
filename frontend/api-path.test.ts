/**
 * Guard: the API prefix has one source, `API_PATH` in `static-paths.ts`.
 *
 * @remarks
 * Fails when frontend code (not a comment) spells the prefix out anywhere but the files below, each
 * with its reason.
 *
 * @packageDocumentation
 */

import { globSync, readFileSync } from 'node:fs';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

import { API_PATH } from './static-paths';

/** Files that may spell the prefix, and why. */
const ALLOWED: Record<string, string> = {
  'static-paths.ts': 'the one source',
  'static-paths.test.ts': "tests the source's matcher on concrete paths",
  'lib/api/types.gen.d.ts': 'generated from the OpenAPI document',
  'lib/api/items.ts': 'indexes the generated types by their path keys',
  'lib/hooks/useItems.ts': 'indexes the generated types by their path keys',
};
/** Installed, generated or reported output, never source. */
const SKIP = new Set(['node_modules', '.next', 'coverage', 'test-results', 'playwright-report']);
const COMMENT = /^\s*(\/\/|\/\*|\*)/;

/**
 * The lines of a source text that spell the prefix outside a comment line.
 * @param text The file's text.
 * @returns The 1-based line numbers.
 */
function literalLines(text: string): number[] {
  return text
    .split('\n')
    .flatMap((line, i) => (line.includes(API_PATH) && !COMMENT.test(line) ? [i + 1] : []));
}

describe('the API prefix', () => {
  it('is spelled out only in static-paths.ts and the allowed files', () => {
    const files = globSync('**/*.{ts,tsx,mts,mjs,js}', {
      cwd: __dirname,
      exclude: (name: string) => SKIP.has(path.basename(name)),
    });
    expect(files).toContain('lib/config.ts');
    const offenders = files
      .filter((file) => !(file in ALLOWED))
      .flatMap((file) =>
        literalLines(readFileSync(path.join(__dirname, file), 'utf8')).map((n) => `${file}:${n}`),
      );
    expect(offenders).toEqual([]);
  });

  it('finds a literal and skips a comment', () => {
    expect(literalLines(`// ${API_PATH}\n * ${API_PATH}\nconst a = '${API_PATH}/x';\n`)).toEqual([
      3,
    ]);
  });
});
