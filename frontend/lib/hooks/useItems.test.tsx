/**
 * Tests for useItems React Query hooks.
 *
 * @packageDocumentation
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw/http';
import React from 'react';
import { describe, expect, it } from 'vitest';

import { server } from '@/src/test/mocks/server';
import { renderHook, waitFor } from '@/src/test/test-utils';

import { useItem, useItems } from './useItems';

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

describe('useItems', () => {
  it('should fetch items', async () => {
    server.use(
      http.get(`${API_BASE}/items`, () => {
        return HttpResponse.json({
          items: [],
          total: 0,
          page: 1,
          page_size: 50,
          total_pages: 0,
        });
      }),
    );

    const { result } = renderHook(() => useItems(), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data?.items).toEqual([]);
    expect(result.current.data?.total).toBe(0);
  });

  it('should pass search parameter to API', async () => {
    let capturedUrl = '';

    server.use(
      http.get(`${API_BASE}/items`, ({ request }) => {
        capturedUrl = request.url;
        return HttpResponse.json({
          items: [],
          total: 0,
          page: 1,
          page_size: 50,
          total_pages: 0,
        });
      }),
    );

    const { result } = renderHook(() => useItems('test'), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(capturedUrl).toContain('search=test');
  });
});

describe('useItem', () => {
  it('should fetch single item', async () => {
    const mockItem = {
      id: '123e4567-e89b-12d3-a456-426614174000',
      name: 'Test',
      description: '',
      is_active: true,
      created_at: '2024-01-01T00:00:00Z',
      updated_at: '2024-01-01T00:00:00Z',
      deleted_at: null,
    };

    server.use(
      http.get(`${API_BASE}/items/:id`, () => {
        return HttpResponse.json(mockItem);
      }),
    );

    const { result } = renderHook(() => useItem('123e4567-e89b-12d3-a456-426614174000'), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(result.current.data?.name).toBe('Test');
  });

  it('should not fetch when id is empty', () => {
    const { result } = renderHook(() => useItem(''), { wrapper: createWrapper() });

    // Should not be fetching
    expect(result.current.isLoading).toBe(false);
    expect(result.current.data).toBeUndefined();
  });
});
