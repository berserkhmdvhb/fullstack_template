/**
 * The one list of paths the server hands out as static files, and so the paths the proxy skips.
 *
 * @remarks
 * Every response that `headers()` or the proxy can reach carries exactly one Content-Security-Policy.
 * A static path gets `default-src 'none'` from the `headers()` rule in `next.config.ts`
 * (`STATIC_HEADER_SOURCES`); every other path gets the page policy from `proxy.ts`. Next.js matches
 * `headers()` sources case-insensitively and the proxy matcher case-sensitively, so every literal in
 * both is written with `anyCase`: the proxy matcher then excludes exactly the static paths, in any
 * letter case, and the two sets are complements (a path that merely looks like one, such as
 * `/favicon.icox`, `/_next/staticfoo` or `/a/.svg`, is not static and goes to the proxy).
 * The static paths are `/_next/static/*`, `/_next/image/*`, `/favicon.ico` and any file with a
 * non-empty name ending in one of the image extensions, at any depth, in any letter case, except
 * under a backend prefix (`BACKEND_PATHS`, in any letter case). The proxy sends only the exact-case
 * prefix to the backend; a case variant such as `/API/v1/x.svg` is a frontend path with the page policy.
 *
 * Exempt: the 308 redirects Next.js itself issues to normalise a path (`//t.svg`, `/a.svg/`,
 * `/_next/image/`). They come before `headers()` and the proxy, so they carry none of these headers.
 * That is accepted: their body is only the target path, a browser follows them instead of rendering
 * them, and turning the normalisation off (`skipTrailingSlashRedirect`) would turn those URLs into
 * 404 pages. `tests/live/test_static_files.py` pins that they stay bare redirects.
 *
 * Next.js needs the matcher in `proxy.ts` as a literal it can analyse at build time, so the literal
 * stays there and `static-paths.test.ts` checks it equals `proxyMatcher()`. Other files in
 * `public/` (`.json`, `.webmanifest`, `.html`, no extension) are not static here: they go through
 * the proxy and carry the page policy, which allows no inline script and no framing.
 *
 * @packageDocumentation
 */

/** The path of the backend's docs page; the proxy hands it the backend's own policy. */
export const DOCS_PATH = '/docs';
/** Same-origin paths the proxy rewrites onto the backend (the one definition; proxy.ts imports it). */
export const BACKEND_PATHS: readonly string[] = ['/api/v1', DOCS_PATH, '/openapi.json'];

/** Directories of build output (the directory itself and everything below it). */
const DIRECTORIES = ['_next/static', '_next/image'] as const;
/** Single files at the root (exactly that path). */
const FILES = ['favicon.ico'] as const;
/** Image types served from `public/`; an adopter's own files of these types are covered too. */
const EXTENSIONS = ['svg', 'png', 'jpg', 'jpeg', 'gif', 'webp'] as const;

/**
 * A pattern that matches the literal in any letter case, letters written as character classes and
 * `.` escaped: the proxy matcher and the header sources then agree whatever flags Next.js compiles
 * them with. Every literal of the matcher goes through it.
 * @param literal The literal text.
 * @returns The pattern, for example `[sS][vV][gG]` or `[dD][oO][cC][sS]`.
 */
const anyCase = (literal: string): string =>
  [...literal]
    .map((c) => {
      if (c === '.') return '\\.';
      return c.toLowerCase() === c.toUpperCase() ? c : `[${c.toLowerCase()}${c.toUpperCase()}]`;
    })
    .join('');

const extensions = EXTENSIONS.map(anyCase).join('|');

// Under a backend prefix nothing is static: the backend answers there with its own headers, and a
// path such as /api/v1/x.SVG must reach it. Nothing is static, either, without a stem: a last
// segment that is only an extension (/.svg, /a/.svg) is not a file.
const backendPrefixes = BACKEND_PATHS.map((p) => anyCase(p.slice(1))).join('|');
const notBackend = `(?!(?:${backendPrefixes})(?:/|$))`;
const imageFile = `${notBackend}(?:.*/)?[^/]+\\.(?:${extensions})`;

/**
 * The proxy matcher: every route except the static paths above.
 * @returns The matcher pattern.
 */
export function proxyMatcher(): string {
  const directories = DIRECTORIES.map(anyCase).join('|');
  const files = FILES.map(anyCase).join('|');
  return `/((?!(?:${directories})(?:/|$)|(?:${files})$|${imageFile}$).*)`;
}

/** The `headers()` sources of the same paths. */
export const STATIC_HEADER_SOURCES: readonly string[] = [
  ...DIRECTORIES.map((dir) => `/${dir}/:path*`),
  ...FILES.map((file) => `/${file}`),
  `/:path(${imageFile})`,
];
