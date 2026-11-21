// Types for eslint.config.mjs's exports (the .mjs is untyped). Keep the two in sync: eslint.config.test.ts checks the names.
import type { Linter } from 'eslint';

export const zodBlock: Linter.Config;
export const zodConfigBlock: Linter.Config;
declare const config: Linter.Config[];
export default config;
