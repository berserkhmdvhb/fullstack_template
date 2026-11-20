/**
 * Health check API route for container orchestration probes.
 *
 * @remarks
 * Provides a lightweight liveness endpoint at `/api/healthz` that returns
 * `{ status: "ok" }` with HTTP 200. Used by Docker HEALTHCHECK directives
 * and load balancer health probes to verify the frontend process is running.
 *
 * This is independent of backend health — it only confirms the Next.js
 * server process is alive and responding.
 *
 * @packageDocumentation
 */

import { NextResponse } from 'next/server';

/**
 * GET /api/healthz
 *
 * @returns A 200 response with `{ status: "ok" }`.
 *
 * @public
 */
export function GET(): NextResponse {
  return NextResponse.json({ status: 'ok' });
}
