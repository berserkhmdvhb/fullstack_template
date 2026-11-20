/**
 * Typed API endpoint functions.
 *
 * @remarks
 * Auto-generated types from `openapi-typescript` ensure type safety.
 * Each function maps to a backend endpoint with full request/response typing.
 *
 * @packageDocumentation
 */

import { API_BASE } from '../config';

import { httpJSON } from './client';
import type { components, operations } from './types.gen';

// Re-export generated types for convenience
export type { components, operations };

/**
 * Health check response type.
 */
export type HealthStatus = components['schemas']['HealthStatus'];

/**
 * Echo request type.
 */
export type EchoRequest = components['schemas']['EchoRequest'];

/**
 * Echo response type.
 */
export type EchoResponse = components['schemas']['EchoResponse'];

/**
 * GET /api/v1/health
 *
 * @returns Health status payload.
 *
 * @public
 */
export async function getHealth(): Promise<HealthStatus> {
  return httpJSON<HealthStatus>(`${API_BASE}/health`, { timeoutMs: 5000 });
}

/**
 * GET /api/v1/healthz
 *
 * @returns Health status payload (for load balancer probes).
 *
 * @public
 */
export async function getHealthz(): Promise<HealthStatus> {
  return httpJSON<HealthStatus>(`${API_BASE}/healthz`, { timeoutMs: 5000 });
}

/**
 * POST /api/v1/echo
 *
 * @param payload - Echo request body.
 * @returns Echo response with text and length.
 *
 * @public
 */
export async function postEcho(payload: EchoRequest): Promise<EchoResponse> {
  return httpJSON<EchoResponse>(`${API_BASE}/echo`, {
    method: 'POST',
    body: JSON.stringify(payload),
    timeoutMs: 10000,
  });
}

/**
 * GET /api/v1/echo/history
 *
 * @returns Array of previously echoed items.
 *
 * @public
 */
export async function getEchoHistory(): Promise<EchoResponse[]> {
  return httpJSON<EchoResponse[]>(`${API_BASE}/echo/history`, { timeoutMs: 10000 });
}

// ─── Items ───────────────────────────────────────────────────────────────────
// Item CRUD lives in `./items` (the canonical SAMPLE/REFERENCE client). It is
// intentionally not duplicated here — import from `@/lib/api` (the barrel) or
// `@/lib/api/items` directly.
