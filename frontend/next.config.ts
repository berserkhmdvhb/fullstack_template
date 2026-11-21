import type { NextConfig } from 'next';

import { STATIC_HEADER_SOURCES } from './static-paths';

// Cache policy of the files in `public/`, the one place it lives (the nginx tier that used to set it is
// retired). `/_next/static/*` is not listed: Next.js already sends `public, max-age=31536000, immutable`
// for the content-hashed build output, and the live test asserts it on a real response.
const CACHE_LONG = 'public, max-age=604800';
const CACHE_REVALIDATE = 'no-cache';
const publicFileCache: ReadonlyArray<readonly [string, string]> = [
  ['/logo.png', CACHE_LONG],
  ['/favicon.ico', CACHE_LONG],
  ['/robots.txt', CACHE_REVALIDATE],
  ['/sitemap.xml', CACHE_REVALIDATE],
  ['/health.txt', CACHE_REVALIDATE],
];

const nextConfig: NextConfig = {
  // The image's version, passed at build time (Dockerfile.frontend); unset in local dev. Next.js stamps it
  // on asset URLs (?dpl=) and navigation responses, so a client from another version reloads instead of
  // mixing assets during a rollout.
  ...(process.env['NEXT_DEPLOYMENT_ID'] ? { deploymentId: process.env['NEXT_DEPLOYMENT_ID'] } : {}),
  reactStrictMode: true,
  poweredByHeader: false,
  output: 'standalone',
  images: { unoptimized: true },
  typedRoutes: true,
  // Same-origin API/docs proxying is handled by proxy.ts (see that file):
  // it reads `BACKEND_URL` per request at runtime. It is intentionally NOT done
  // here — `next.config.ts` `rewrites()` destinations are baked into
  // routes-manifest.json at build time, so a runtime env var could never reach
  // the standalone server.
  async headers() {
    if (process.env.NODE_ENV !== 'production') return [];
    // Security headers applied in production only
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'Referrer-Policy', value: 'no-referrer' },
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'Permissions-Policy', value: 'geolocation=(), microphone=(), camera=()' },
          // Cross-origin isolation (OWASP secure-headers baseline); deliberately no COEP.
          { key: 'Cross-Origin-Opener-Policy', value: 'same-origin' },
          { key: 'Cross-Origin-Resource-Policy', value: 'same-origin' },
          // Strict-Transport-Security is not set here: HSTS is set once, at the edge
          // (until the edge sets it, the backend sends it over https).
          // Content-Security-Policy is set per request in `proxy.ts`, not
          // here: it carries a fresh nonce on every render (see that file), and a
          // static build-time header from `headers()` could never provide one.
          // The directive rationale (no CDN, same-origin connect, no `unsafe-*`)
          // lives next to the header in `proxy.ts`.
        ],
      },
      // Files the proxy skips (see static-paths.ts) carry no page, so nothing may frame them and no
      // content policy lets anything run from them; this is what the retired static tier sent. No
      // duplicate Content-Security-Policy can arise: the proxy never runs on these paths.
      ...STATIC_HEADER_SOURCES.map((source) => ({
        source,
        headers: [
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'Content-Security-Policy', value: "default-src 'none'" },
        ],
      })),
      // The proxied text files (they carry the page policy) cannot be framed either.
      ...publicFileCache.map(([source, value]) => ({
        source,
        headers: [
          { key: 'Cache-Control', value },
          ...(value === CACHE_REVALIDATE ? [{ key: 'X-Frame-Options', value: 'DENY' }] : []),
        ],
      })),
    ];
  },
};

export default nextConfig;
