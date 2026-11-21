/**
 * The zod import rule: `zod` may be imported only by `lib/zod-config.ts`.
 *
 * @remarks
 * Runs the rule's own config blocks through a minimal ESLint (TypeScript parser, no project
 * service, no other plugin), so the result depends only on those blocks: not on the working tree,
 * build output or the cost of loading the full config. A separate case checks that the real
 * config contains the very same blocks.
 *
 * @packageDocumentation
 */

import { ESLint } from 'eslint';
import tseslint from 'typescript-eslint';
import { describe, expect, it } from 'vitest';

import fullConfig, { zodBlock, zodConfigBlock } from './eslint.config.mjs';

const eslint = new ESLint({
  overrideConfigFile: true,
  overrideConfig: [
    { files: ['**/*.ts'], languageOptions: { parser: tseslint.parser } },
    zodBlock,
    zodConfigBlock,
  ],
});

const lint = async (filePath: string, code: string) => {
  const [result] = await eslint.lintText(code, { filePath });
  return (result?.messages ?? []).map((m) => m.ruleId);
};

describe('no-restricted-imports for zod', () => {
  it.each(["import { z } from 'zod';", "import * as z from 'zod/v4';"])(
    'rejects %s outside lib/zod-config.ts',
    async (code) => {
      expect(await lint('features/items/lib/schemas.ts', `${code}\nexport { z };\n`)).toEqual([
        'no-restricted-imports',
      ]);
    },
  );

  it('accepts the re-export and packages that merely end in zod', async () => {
    const code =
      "import { z } from '@/lib/zod-config';\nimport { zodResolver } from '@hookform/resolvers/zod';\nexport { z, zodResolver };\n";
    expect(await lint('features/items/lib/schemas.ts', code)).toEqual([]);
  });

  it('lets lib/zod-config.ts itself import zod', async () => {
    const code = "import { z } from 'zod';\nz.config({ jitless: true });\nexport { z };\n";
    expect(await lint('lib/zod-config.ts', code)).toEqual([]);
  });

  it('is part of the real ESLint config, in this order', () => {
    const at = (block: object) => (fullConfig as object[]).indexOf(block);
    expect(at(zodBlock)).toBeGreaterThanOrEqual(0);
    expect(at(zodConfigBlock)).toBeGreaterThan(at(zodBlock));
  });
});
