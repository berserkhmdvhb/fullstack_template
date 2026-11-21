/**
 * Vitest configuration for unit tests.
 *
 * @remarks
 * - No DOM environment (tests don't render or touch the DOM)
 * - No setup file (no MSW, no Next.js mocks, no jest-dom)
 * - Fast execution: pure logic tests only
 *
 * @packageDocumentation
 */

import path from 'node:path';

import { defineConfig } from 'vitest/config';

export default defineConfig({
  resolve: {
    alias: {
      '@': path.resolve(__dirname, '.'),
    },
  },
  test: {
    globals: true,
    include: [
      'lib/config.test.ts',
      'lib/api/error-map.test.ts',
      'features/items/lib/schemas.test.ts',
      'styles/globals.test.ts',
    ],
  },
});
