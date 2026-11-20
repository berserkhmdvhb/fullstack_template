// eslint.config.mjs
import { defineConfig } from 'eslint/config';

import nextPlugin from '@next/eslint-plugin-next';
import reactPlugin from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import a11yPlugin from 'eslint-plugin-jsx-a11y';
import tseslint from 'typescript-eslint';
import pluginImport from 'eslint-plugin-import';
import jsdoc from 'eslint-plugin-jsdoc';
import testingLibrary from 'eslint-plugin-testing-library';
import vitest from 'eslint-plugin-vitest';

// ---- 1) Non-typed TS block: parse TS with ts parser, no project ----
const nonTypedTsBlock = {
  files: ['**/*.{ts,tsx}'],
  plugins: {
    '@typescript-eslint': tseslint.plugin,
    react: reactPlugin,
    'react-hooks': reactHooks,
    'jsx-a11y': a11yPlugin,
    import: pluginImport,
    '@next/next': nextPlugin,
  },
  languageOptions: {
    parser: tseslint.parser,
  },
  settings: {
    react: { version: 'detect' },
    'import/resolver': {
      typescript: { project: './tsconfig.json', alwaysTryTypes: true },
    },
  },
  rules: {
    // React + Hooks + a11y + Next
    ...reactPlugin.configs.flat.recommended.rules,
    ...reactHooks.configs.flat.recommended.rules,
    ...(a11yPlugin.configs.recommended?.rules ?? {}),
    ...nextPlugin.configs.recommended.rules,
    ...nextPlugin.configs['core-web-vitals'].rules,

    // Non-typed TS rules
    '@typescript-eslint/no-explicit-any': 'error',
    '@typescript-eslint/no-unused-vars': [
      'warn',
      { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
    ],
    '@typescript-eslint/array-type': ['error', { default: 'array-simple' }],
    '@typescript-eslint/consistent-type-definitions': ['error', 'type'],
    '@typescript-eslint/consistent-type-assertions': ['error', { assertionStyle: 'as' }],

    'import/no-cycle': 'warn',

    // JS safety
    'no-console': ['warn', { allow: ['warn', 'error', 'info', 'debug'] }],
    'no-async-promise-executor': 'error',
    'no-unsafe-finally': 'error',
    'no-return-await': 'error',
    'no-constant-condition': ['error', { checkLoops: false }],
    'prefer-const': 'error',

    // Import hygiene
    'import/order': [
      'warn',
      {
        alphabetize: { order: 'asc', caseInsensitive: true },
        'newlines-between': 'ignore',
        groups: ['builtin', 'external', 'internal', 'parent', 'sibling', 'index', 'object'],
      },
    ],
    // React 17+ automatic JSX runtime: React import not required
    'react/react-in-jsx-scope': 'off',
    'react/jsx-uses-react': 'off',
  },
};

// ---- 2) Typed TS block: type-aware rules (excludes test files) ----
const typedTsBlock = {
  files: ['**/*.{ts,tsx}'],
  ignores: ['**/*.test.{ts,tsx}', '**/*.spec.{ts,tsx}', 'src/test/**', 'lib/api/types.gen.d.ts'],
  languageOptions: {
    parser: tseslint.parser,
    parserOptions: {
      project: './tsconfig.json',
    },
  },
  plugins: {
    '@typescript-eslint': tseslint.plugin,
  },
  rules: {
    '@typescript-eslint/no-floating-promises': 'error',
    '@typescript-eslint/no-misused-promises': 'error',
    '@typescript-eslint/await-thenable': 'error',
    '@typescript-eslint/no-unnecessary-type-assertion': 'error',
  },
};

// ---- 3) Ignores ----
const ignoreBlock = {
  ignores: [
    '.next/**',
    'out/**',
    'build/**',
    'node_modules/**',
    'dist/**',
    'eslint.config.*',
    'postcss.config.js',
    'next-env.d.ts',
    'lib/api/types.gen.d.ts',
    'lib/api/types.gen.ts',
  ],
};

// ---- 4) Test files: testing-library + vitest ----
const testBlock = {
  files: ['**/*.{test,spec}.{ts,tsx}'],
  plugins: {
    'testing-library': testingLibrary,
    vitest,
  },
  rules: {
    ...testingLibrary.configs['flat/react'].rules,
    'vitest/no-conditional-tests': 'error',
    'vitest/expect-expect': 'warn',
  },
};

// ---- 5) jsdoc ----
const jsdocBlock = {
  plugins: { jsdoc },
  settings: { jsdoc: { mode: 'typescript' } },
  rules: {
    'jsdoc/check-alignment': 'warn',
    'jsdoc/check-indentation': 'warn',
    'jsdoc/require-jsdoc': [
      'warn',
      {
        contexts: [
          'TSInterfaceDeclaration[export=true]',
          'TSTypeAliasDeclaration[export=true]',
          'ClassDeclaration[export=true]',
          'FunctionDeclaration[export=true]',
          'ExportNamedDeclaration > VariableDeclaration > VariableDeclarator[init.type="ArrowFunctionExpression"]',
        ],
        require: {
          ClassDeclaration: true,
          FunctionDeclaration: true,
          MethodDefinition: false,
          ArrowFunctionExpression: false,
        },
      },
    ],
  },
};

export default defineConfig([
  {
    name: 'next/core-web-vitals',
  },
  nonTypedTsBlock,
  typedTsBlock,
  testBlock,
  jsdocBlock,
  ignoreBlock,
]);
