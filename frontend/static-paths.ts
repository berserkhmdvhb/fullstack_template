/**
 * The one list of paths the server hands out as static files, and so the paths the proxy skips.
 *
 * @remarks
 * Every response carries exactly one Content-Security-Policy. A static path gets
 * `default-src 'none'` from the `headers()` rule in `next.config.ts` (`STATIC_HEADER_SOURCES`);
 * every other path gets the page policy from `proxy.ts`. The proxy matcher excludes exactly the
 * static paths, so the two sets are complements and no path falls between them (a path that merely
 * looks like one, such as `/favicon.icox` or `/_next/staticfoo`, is not static: it goes to the
 * proxy). The static paths are `/_next/static/*`, `/_next/image/*`, `/favicon.ico` and any file
 * ending in one of the image extensions, at any depth, in any letter case.
 *
 * Next.js needs the matcher in `proxy.ts` as a literal it can analyse at build time, so the literal
 * stays there and `static-paths.test.ts` checks it equals `proxyMatcher()`. Other files in
 * `public/` (`.json`, `.webmanifest`, `.html`, no extension) are not static here: they go through
 * the proxy and carry the page policy, which allows no inline script and no framing.
 *
 * @packageDocumentation
 */

/** Directories of build output (the directory itself and everything below it). */
const DIRECTORIES = ['_next/static', '_next/image'] as const;
/** Single files at the root (exactly that path). */
const FILES = ['favicon.ico'] as const;
/** Image types served from `public/`; an adopter's own files of these types are covered too. */
const EXTENSIONS = ['svg', 'png', 'jpg', 'jpeg', 'gif', 'webp'] as const;

/**
 * A pattern that matches the word in any letter case, written as character classes: the proxy
 * matcher and the header sources then agree whatever flags Next.js compiles them with.
 * @param word The lower-case word.
 * @returns The pattern, for example `[sS][vV][gG]`.
 */
const anyCase = (word: string): string =>
  [...word].map((c) => `[${c.toLowerCase()}${c.toUpperCase()}]`).join('');

const extensions = EXTENSIONS.map(anyCase).join('|');

/**
 * The proxy matcher: every route except the static paths above.
 * @returns The matcher pattern.
 */
export function proxyMatcher(): string {
  const directories = DIRECTORIES.join('|');
  const files = FILES.map((file) => file.replace('.', '\\.')).join('|');
  return `/((?!(?:${directories})(?:/|$)|(?:${files})$|.*\\.(?:${extensions})$).*)`;
}

/** The `headers()` sources of the same paths. */
export const STATIC_HEADER_SOURCES: readonly string[] = [
  ...DIRECTORIES.map((dir) => `/${dir}/:path*`),
  ...FILES.map((file) => `/${file}`),
  `/:path*.(${extensions})`,
];
