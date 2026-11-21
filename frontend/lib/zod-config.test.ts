import { describe, expect, it } from 'vitest';
import '@/features/items/lib/schemas';
import { z } from '@/lib/zod-config';

describe('zod configuration', () => {
  it('is jitless once the schemas are loaded, so zod never probes new Function', () => {
    expect(z.core.globalConfig.jitless).toBe(true);
  });
});
