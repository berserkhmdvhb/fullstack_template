/**
 * Unit tests for the HTTP client (httpJSON).
 *
 * @packageDocumentation
 */

import { http, HttpResponse } from 'msw/http';
import { describe, expect, it, vi } from 'vitest';

import { httpJSON } from '@/lib/api/client';
import { server } from '@/src/test/mocks/server';
import { API_PATH as API_BASE } from '@/static-paths';

describe('httpJSON', () => {
  it('should fetch and parse JSON response', async () => {
    const mockData = { status: 'ok' };

    server.use(
      http.get(`${API_BASE}/test`, () => {
        return HttpResponse.json(mockData);
      }),
    );

    const result = await httpJSON<{ status: string }>(`${API_BASE}/test`);

    expect(result).toEqual(mockData);
  });

  it('should throw HttpError on non-OK response', async () => {
    server.use(
      http.get(`${API_BASE}/error`, () => {
        return HttpResponse.json({ detail: 'Not found' }, { status: 404 });
      }),
    );

    await expect(httpJSON(`${API_BASE}/error`)).rejects.toThrow();
  });

  it('should throw HttpError on non-JSON response', async () => {
    server.use(
      http.get(`${API_BASE}/text`, () => {
        return new HttpResponse('plain text', {
          status: 200,
          headers: { 'Content-Type': 'text/plain' },
        });
      }),
    );

    await expect(httpJSON(`${API_BASE}/text`)).rejects.toThrow();
  });

  it('should abort request on timeout', async () => {
    vi.useFakeTimers();

    // Mock fetch that respects abort signal
    vi.stubGlobal(
      'fetch',
      vi.fn((_url: string, init?: RequestInit) => {
        const signal = init?.signal;
        return new Promise((_resolve, reject) => {
          if (signal) {
            if (signal.aborted) {
              reject(new DOMException('Aborted', 'AbortError'));
            } else {
              signal.addEventListener('abort', () => {
                reject(new DOMException('Aborted', 'AbortError'));
              });
            }
          }
          // Never resolves otherwise (simulates slow server)
        });
      }),
    );

    const promise = httpJSON(`${API_BASE}/slow`, { timeoutMs: 100 });

    // Advance timers past the timeout
    vi.advanceTimersByTime(200);

    await expect(promise).rejects.toThrow();

    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('should set Content-Type header on requests', async () => {
    let capturedContentType: string | null = null;

    server.use(
      http.post(`${API_BASE}/headers`, ({ request }) => {
        capturedContentType = request.headers.get('Content-Type');
        return HttpResponse.json({});
      }),
    );

    await httpJSON(`${API_BASE}/headers`, { method: 'POST' });

    expect(capturedContentType).toBe('application/json');
  });

  it('should clean up timeout timer on success', async () => {
    const clearTimeoutSpy = vi.spyOn(global, 'clearTimeout');

    // Mock fetch to return immediately
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(new Response(JSON.stringify({}), { status: 200 }))),
    );

    await httpJSON(`${API_BASE}/fast`, { timeoutMs: 5000 });

    expect(clearTimeoutSpy).toHaveBeenCalled();
    clearTimeoutSpy.mockRestore();
    vi.unstubAllGlobals();
  });
});
