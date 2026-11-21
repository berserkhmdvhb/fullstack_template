/**
 * Global zod configuration; import it first from every module that builds zod schemas.
 *
 * @remarks
 * Zod 4 probes `new Function("")` to see whether it may compile fast-path parsers. A strict
 * Content-Security-Policy (no `'unsafe-eval'`) blocks the probe and the browser reports a
 * `securitypolicyviolation` even though zod catches the error. `jitless` skips the probe and the
 * compiled parsers (zod's documented switch: https://zod.dev/api#jitless). Schemas parse the
 * same either way.
 *
 * @packageDocumentation
 */

import { z } from 'zod';

z.config({ jitless: true });

/** The configured zod: import `z` from here, never from `zod` (an ESLint rule enforces it). */
export { z };
