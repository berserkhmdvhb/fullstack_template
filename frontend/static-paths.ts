/**
 * The one list of paths the server hands out as static files and the proxy therefore skips.
 *
 * @remarks
 * Two things read it: `next.config.ts` (the header rule that locks these files down) and, through
 * `proxyMatcher()`, the matcher in `proxy.ts`. Next.js needs that matcher as a literal it can
 * analyse at build time, so the literal stays there and `static-paths.test.ts` checks it equals
 * what this list builds. A path added here without the matcher (or the other way round) fails that
 * test, so no file can be skipped by the proxy yet left without the static headers.
 *
 * @packageDocumentation
 */

/** Directories of build output. */
const DIRECTORIES = ['_next/static', '_next/image'] as const;
/** Single files at the root. */
const FILES = ['favicon.ico'] as const;
/** Image types served from `public/`; an adopter's own files of these types are covered too. */
const EXTENSIONS = ['svg', 'png', 'jpg', 'jpeg', 'gif', 'webp'] as const;

/**
 * The proxy matcher: every route except the static paths above.
 * @returns The matcher pattern.
 */
export function proxyMatcher(): string {
  return `/((?!${[...DIRECTORIES, ...FILES].join('|')}|.*\\.(?:${EXTENSIONS.join('|')})$).*)`;
}

/** The `headers()` sources of the same paths. */
export const STATIC_HEADER_SOURCES: readonly string[] = [
  ...DIRECTORIES.map((dir) => `/${dir}/:path*`),
  ...FILES.map((file) => `/${file}`),
  `/:path*.(${EXTENSIONS.join('|')})`,
];
