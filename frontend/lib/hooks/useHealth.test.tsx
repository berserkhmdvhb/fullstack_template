/**
 * Tests for the useHealth React Query hook.
 *
 * @packageDocumentation
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw/http';
import React from 'react';
import { describe, expect, it } from 'vitest';

import { server } from '@/src/test/mocks/server';
import { renderHook, waitFor } from '@/src/test/test-utils';

import { useHealth } from './useHealth';

const API_BASE = '/api/v1';

/** Creates a test wrapper with QueryClientProvider. */
function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });

  /** Wrapper component that provides the query client. */
  function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  }

  return Wrapper;
}

describe('useHealth', () => {
  it('should report the backend healthy', async () => {
    server.use(
      http.get(`${API_BASE}/health`, () => {
        return HttpResponse.json({ status: 'ok' });
      }),
    );

    const { result } = renderHook(() => useHealth(), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data?.status).toBe('ok');
  });

  it('should report the backend unreachable on error', async () => {
    server.use(
      http.get(`${API_BASE}/health`, () => {
        return new HttpResponse(null, { status: 503 });
      }),
    );

    const { result } = renderHook(() => useHealth(), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });
  });
});
