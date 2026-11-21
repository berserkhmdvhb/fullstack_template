/**
 * Vitest configuration for the whole suite: the one place coverage is measured and its thresholds live.
 *
 * @remarks
 * - Runs every test file (the unit-only files too), so one coverage run counts them all
 * - Environment: happy-dom (simulates browser DOM)
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
      // All application code: the routes (including the API route handlers), shared components, features,
      // the library and the proxy.
      include: [
        'app/**/*.{ts,tsx}',
        'components/**/*.{ts,tsx}',
        'features/**/*.{ts,tsx}',
        'lib/**/*.{ts,tsx}',
        'proxy.ts',
      ],
      exclude: [
        // Tests.
        '**/*.test.{ts,tsx}',
        '**/*.spec.{ts,tsx}',
        'src/test/**',
        // Playwright specs run in the browser job, not here.
        'e2e/**',
        // Type declarations and generated code (the OpenAPI types).
        '**/*.d.ts',
        '**/*.gen.ts',
      ],
    },
  },
});
