/**
 * Vitest configuration for integration tests.
 *
 * @remarks
 * - Environment: happy-dom (simulates browser DOM)
 * - Setup: pre-wired test utilities with all providers + MSW
 * - Includes: Component tests, hook tests, API client tests with MSW
 * - Excludes: Pure unit tests (config, schemas, error-map)
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
    environment: 'happy-dom',
    globals: true,
    setupFiles: ['./src/test/setup.tsx'],
    include: ['**/*.{test,spec}.{ts,tsx}'],
    exclude: [
      '**/node_modules/**',
      '**/.next/**',
      '**/e2e/**',
      'lib/config.test.ts',
      'lib/api/error-map.test.ts',
      'features/items/lib/schemas.test.ts',
    ],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'lcov'],
      thresholds: {
        lines: 80,
        functions: 80,
        statements: 80,
        branches: 70,
      },
      include: ['lib/**/*.{ts,tsx}', 'app/**/*.{ts,tsx}', 'features/**/*.{ts,tsx}'],
      exclude: ['lib/api/types.gen.d.ts', '**/*.test.{ts,tsx}', '**/*.spec.{ts,tsx}', 'app/api/**'],
    },
  },
});
