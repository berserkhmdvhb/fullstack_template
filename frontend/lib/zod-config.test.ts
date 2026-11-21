import { describe, expect, it } from 'vitest';
import { z } from 'zod';
import '@/features/items/lib/schemas';

describe('zod configuration', () => {
  it('is jitless once the schemas are loaded, so zod never probes new Function', () => {
    expect(z.core.globalConfig.jitless).toBe(true);
  });
});
