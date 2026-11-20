import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  output: 'standalone',
  images: { unoptimized: true },
  typedRoutes: true,
  // Same-origin API/docs proxying is handled by middleware.ts (see that file):
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
          {
            key: 'Strict-Transport-Security',
            value: 'max-age=15552000; includeSubDomains; preload',
          },
          // Content-Security-Policy is set per request in `middleware.ts`, not
          // here: it carries a fresh nonce on every render (see that file), and a
          // static build-time header from `headers()` could never provide one.
          // The directive rationale (no CDN, same-origin connect, no `unsafe-*`)
          // lives next to the header in `middleware.ts`.
        ],
      },
    ];
  },
};

export default nextConfig;
