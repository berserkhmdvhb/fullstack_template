import type { NextRequest } from 'next/server';
import { NextResponse } from 'next/server';

// Runtime backend proxy.
//
// The browser calls same-origin `/api/v1/...`, `/docs` and `/openapi.json`;
// this proxy rewrites them to the backend at `BACKEND_URL`. `/docs` is the
// backend's own Swagger UI page (its pinned bundle loads from the jsDelivr CDN).
//
// WHY A PROXY AND NOT `rewrites()` IN next.config.ts
//   Next.js evaluates `rewrites()` during `next build` and freezes the concrete
//   destinations into `.next/routes-manifest.json`. A `process.env` reference
//   there captures whatever env was present at *build* time, and the standalone
//   server serves that baked manifest at runtime — so a runtime container env
//   like `BACKEND_URL` can never reach it (this is exactly what made the image
//   proxy to the value baked in at build, e.g. a developer's local `.env.local`).
//   The proxy runs per request in the Node runtime and reads the *live* env,
//   which is what lets one image target any backend (local, compose, k8s).
//
// `BACKEND_URL` defaults to a local dev backend on :8000. The frontend's own
// liveness probe at `/api/healthz` is intentionally NOT matched (it is not under
// `/api/v1`), so it keeps being served by the frontend itself.
const resolveBackend = () =>
  (process.env['BACKEND_URL'] || 'http://localhost:8000').replace(/\/+$/, '');

// Same-origin paths rewritten onto the backend. `/api/healthz` is deliberately
// NOT here (it is the frontend's own liveness probe and must stay served by the
// frontend), so it falls through to `NextResponse.next()` below.
const DOCS_PATH = '/docs';
const BACKEND_PATHS = ['/api/v1', DOCS_PATH, '/openapi.json'];
const proxiesToBackend = (pathname: string) =>
  BACKEND_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`));

// CSP violation reports go to the backend's opt-in endpoint (same-origin, so `'self'` already allows
// it). The backend serves it only when its CSP_REPORT_ENABLED is on; this flag, read per request,
// is the frontend half (the chart sets both from one value).
const CSP_REPORT_PATH = '/api/v1/csp-report';
const CSP_REPORT_GROUP = 'csp-endpoint';
const cspReportingEnabled = () =>
  (process.env['CSP_REPORTING_ENABLED'] ?? '').trim().toLowerCase() === 'true';

/**
 * Per request this proxy does two things.
 *
 * First, it rewrites same-origin API/docs requests onto the live backend
 * (`BACKEND_URL`) — see the header comment for why this is a proxy and not
 * next.config `rewrites()` (a build-time rewrite destination could never see a
 * runtime container env).
 *
 * Second, it installs a per-request Content-Security-Policy with a fresh nonce.
 * Next.js App Router bootstraps via inline `<script>` tags (the RSC
 * `self.__next_f` payload), which a `script-src 'self'` policy with no
 * `'unsafe-inline'` would block — blanking the page. A static header from
 * next.config can never carry a per-render nonce, so the CSP lives here: a fresh
 * `crypto` nonce is minted per request, put on `script-src` as `'nonce-<…>'`,
 * and Next's renderer reads it straight back out of the CSP header to allow its
 * own inline scripts — no `'unsafe-inline'`/`'unsafe-eval'` needed. The same
 * nonce is exposed to the root layout via `x-nonce` for `<html nonce>`.
 */
export function proxy(request: NextRequest) {
  const nonce = btoa(crypto.randomUUID());
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set('x-nonce', nonce);

  const { nextUrl } = request;
  const response = proxiesToBackend(nextUrl.pathname)
    ? NextResponse.rewrite(
        new URL(`${resolveBackend()}${nextUrl.pathname}${nextUrl.search}`, nextUrl),
        { request: { headers: requestHeaders } },
      )
    : NextResponse.next({ request: { headers: requestHeaders } });

  // The docs page and its init script are the backend's, and so is their CSP (the one page that
  // admits a third-party origin, jsDelivr, for SRI-pinned assets). Pass it through: a second CSP here
  // would make two policies, and the browser would enforce both.
  if (nextUrl.pathname === DOCS_PATH || nextUrl.pathname.startsWith(`${DOCS_PATH}/`)) {
    return response;
  }

  const reporting = cspReportingEnabled();
  response.headers.set(
    'Content-Security-Policy',
    [
      "default-src 'self'",
      // No third-party script/style origin on any page this proxy serves a CSP for.
      "img-src 'self' blob: data:",
      "style-src 'self'",
      // The nonce admits Next.js's own inline bootstrap scripts without a blanket
      // 'unsafe-inline'/'unsafe-eval'.
      `script-src 'self' 'nonce-${nonce}'`,
      // The frontend calls the API same-origin (/api/v1, rewritten above), so
      // 'self' is the only connect origin — no broad `https:` that would permit
      // exfiltration to any endpoint.
      "connect-src 'self'",
      "frame-ancestors 'none'",
      "base-uri 'self'",
      // `report-uri` for browsers that only know it, `report-to` (with the
      // Reporting-Endpoints header below) for the rest.
      ...(reporting ? [`report-uri ${CSP_REPORT_PATH}`, `report-to ${CSP_REPORT_GROUP}`] : []),
    ].join('; '),
  );
  if (reporting) {
    response.headers.set('Reporting-Endpoints', `${CSP_REPORT_GROUP}="${CSP_REPORT_PATH}"`);
  }

  return response;
}

export const config = {
  // Run on every route except the static paths (build output, the favicon, image files), which are
  // served straight from disk and carry the static policy that next.config.ts sets instead. Every
  // other path, look-alikes such as /favicon.icox included, gets the page policy here. Next.js needs
  // this as a literal; static-paths.ts is the list it is built from, and a test checks they are equal.
  matcher: [
    '/((?!(?:_next/static|_next/image)(?:/|$)|(?:favicon\\.ico)$|.*\\.(?:[sS][vV][gG]|[pP][nN][gG]|[jJ][pP][gG]|[jJ][pP][eE][gG]|[gG][iI][fF]|[wW][eE][bB][pP])$).*)',
  ],
};
