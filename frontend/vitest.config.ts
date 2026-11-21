/**
 * Vitest configuration for unit and integration tests.
 *
 * @remarks
 * - Environment: jsdom (simulates browser DOM)
 * - Globals: describe, it, expect available without explicit import
 * - Setup: pre-wired test utilities with all providers
 * - Coverage: 80% lines/functions/statements, 70% branches
 * - Alias: @/* maps to ./* (matching tsconfig.json)
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
    // Node 25+ has its own global localStorage (undefined without --localstorage-file) that shadows the DOM's.
    execArgv: ['--no-experimental-webstorage'],
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
      exclude: [
        'lib/api/types.gen.d.ts',
        '**/*.test.{ts,tsx}',
        '**/*.spec.{ts,tsx}',
        'app/api/**',
      ],
    },
  },
});
