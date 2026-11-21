/**
 * Tests for the frontend liveness route.
 *
 * @packageDocumentation
 */

import { describe, expect, it } from 'vitest';

import { GET } from './route';

describe('GET /api/healthz', () => {
  it('answers 200 with an ok status and no backend call', async () => {
    const response = GET();

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ status: 'ok' });
  });
});
