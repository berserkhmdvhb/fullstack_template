// lib/config.ts

/**
 * The API base the browser calls.
 *
 * Same-origin and versioned: the browser fetches `/api/v1/...` from its own
 * origin, and the Next.js server proxies those calls to the backend (see
 * `rewrites()` in next.config.ts, driven by the runtime `BACKEND_URL` env).
 * Because the URL is relative, one built bundle works in every environment —
 * the backend location is a deploy-time env var, not a build-time constant.
 *
 * `NEXT_PUBLIC_API_URL` is only consulted for an explicit override; leave it
 * unset to use the same-origin default.
 */
export const API_BASE = process.env['NEXT_PUBLIC_API_URL'] ?? '/api/v1';
