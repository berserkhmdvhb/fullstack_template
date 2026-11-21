/**
 * Integration tests for typed API endpoint functions.
 *
 * @packageDocumentation
 */

import { http, HttpResponse } from 'msw/http';
import { describe, expect, it, afterEach } from 'vitest';

import { getEchoHistory, getHealth, getHealthz, postEcho } from '@/lib/api/endpoints';
import { server } from '@/src/test/mocks/server';

const API_BASE = '/api/v1';

describe('API Endpoints', () => {
  afterEach(() => {
    server.resetHandlers();
  });

  describe('getHealth', () => {
    it('should fetch health status', async () => {
      server.use(
        http.get(`${API_BASE}/health`, () => {
          return HttpResponse.json({ status: 'ok' });
        }),
      );

      const result = await getHealth();

      expect(result).toEqual({ status: 'ok' });
    });
  });

  describe('getHealthz', () => {
    it('should fetch healthz status', async () => {
      server.use(
        http.get(`${API_BASE}/healthz`, () => {
          return HttpResponse.json({ status: 'ok' });
        }),
      );

      const result = await getHealthz();

      expect(result).toEqual({ status: 'ok' });
    });
  });

  describe('postEcho', () => {
    it('should post echo and return response', async () => {
      server.use(
        http.post(`${API_BASE}/echo`, async ({ request }) => {
          const body = await request.json();
          return HttpResponse.json({ text: body.text, length: body.text.length }, { status: 201 });
        }),
      );

      const result = await postEcho({ text: 'hello' });

      expect(result.text).toBe('hello');
      expect(result.length).toBe(5);
    });
  });

  describe('getEchoHistory', () => {
    it('should fetch echo history', async () => {
      server.use(
        http.get(`${API_BASE}/echo/history`, () => {
          return HttpResponse.json([
            { text: 'first', length: 5 },
            { text: 'second', length: 6 },
          ]);
        }),
      );

      const result = await getEchoHistory();

      expect(result).toHaveLength(2);
      expect(result[0].text).toBe('first');
    });
  });
});
