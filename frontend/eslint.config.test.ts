/**
 * The zod import rule: `zod` may be imported only by `lib/zod-config.ts`.
 *
 * @packageDocumentation
 */

import { ESLint } from 'eslint';
import { describe, expect, it } from 'vitest';

const lint = async (filePath: string, code: string) => {
  const eslint = new ESLint({ overrideConfigFile: 'eslint.config.mjs' });
  const [result] = await eslint.lintText(code, { filePath });
  return (result?.messages ?? []).map((m) => m.ruleId);
};

describe('no-restricted-imports for zod', () => {
  it.each(["import { z } from 'zod';", "import * as z from 'zod/v4';"])(
    'rejects %s outside lib/zod-config.ts',
    async (code) => {
      expect(await lint('features/items/lib/schemas.ts', `${code}\nexport { z };\n`)).toContain(
        'no-restricted-imports',
      );
    },
    30_000,
  );

  it('accepts the re-export', async () => {
    const code = "import { z } from '@/lib/zod-config';\nexport const s = z.string();\n";
    expect(await lint('features/items/lib/schemas.ts', code)).not.toContain(
      'no-restricted-imports',
    );
  }, 30_000);

  it('lets lib/zod-config.ts itself import zod', async () => {
    const code = "import { z } from 'zod';\nz.config({ jitless: true });\nexport { z };\n";
    expect(await lint('lib/zod-config.ts', code)).not.toContain('no-restricted-imports');
  }, 30_000);
});
