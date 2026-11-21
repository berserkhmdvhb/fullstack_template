import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
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
    ];
  },
};

export default nextConfig;
