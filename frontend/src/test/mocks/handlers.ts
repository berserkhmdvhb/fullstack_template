/**
 * MSW request handlers for API mocking in tests.
 *
 * @remarks
 * Defines mock responses for all backend API endpoints.
 * Handlers can be overridden in individual tests using `server.use()`.
 *
 * @packageDocumentation
 */

import { http, HttpResponse } from 'msw/http';

const API_BASE = '/api/v1';

/**
 * Mock handlers for all API endpoints.
 *
 * @public
 */
export const handlers = [
  // GET /api/v1/health
  http.get(`${API_BASE}/health`, () => {
    return HttpResponse.json({
      status: 'ok',
      version: '0.1.0',
    });
  }),

  // GET /api/v1/healthz
  http.get(`${API_BASE}/healthz`, () => {
    return HttpResponse.json({
      status: 'ok',
    });
  }),

  // POST /api/v1/echo
  http.post(`${API_BASE}/echo`, async ({ request }) => {
    const body = await request.json();
    return HttpResponse.json({
      message: (body as { message?: string }).message ?? '',
      timestamp: new Date().toISOString(),
    });
  }),

  // GET /api/v1/echo/history
  http.get(`${API_BASE}/echo/history`, () => {
    return HttpResponse.json([]);
  }),

  // GET /api/v1/items
  http.get(`${API_BASE}/items`, ({ request }) => {
    const url = new URL(request.url);
    const search = url.searchParams.get('search');
    // Return mock items, optionally filtered by search
    const allItems = [
      {
        id: '11111111-1111-1111-1111-111111111111',
        name: 'Alpha Item',
        description: 'First test item',
        is_active: true,
        created_at: '2025-01-01T00:00:00Z',
        updated_at: '2025-01-01T00:00:00Z',
      },
      {
        id: '22222222-2222-2222-2222-222222222222',
        name: 'Beta Item',
        description: 'Second test item',
        is_active: false,
        created_at: '2025-01-02T00:00:00Z',
        updated_at: '2025-01-02T00:00:00Z',
      },
    ];
    const filtered = search
      ? allItems.filter(
          (i) =>
            i.name.toLowerCase().includes(search.toLowerCase()) ||
            i.description.toLowerCase().includes(search.toLowerCase()),
        )
      : allItems;
    return HttpResponse.json({ items: filtered, total: filtered.length });
  }),

  // GET /api/v1/items/:id
  http.get(`${API_BASE}/items/:id`, ({ params }) => {
    const id = params.id as string;
    if (id === '11111111-1111-1111-1111-111111111111') {
      return HttpResponse.json({
        id,
        name: 'Alpha Item',
        description: 'First test item',
        is_active: true,
        created_at: '2025-01-01T00:00:00Z',
        updated_at: '2025-01-01T00:00:00Z',
      });
    }
    return new HttpResponse(null, { status: 404, statusText: 'Not Found' });
  }),

  // POST /api/v1/items
  http.post(`${API_BASE}/items`, async ({ request }) => {
    const body = (await request.json()) as {
      name: string;
      description?: string;
      is_active?: boolean;
    };
    return HttpResponse.json(
      {
        id: '33333333-3333-3333-3333-333333333333',
        name: body.name,
        description: body.description ?? '',
        is_active: body.is_active ?? true,
        created_at: '2025-01-03T00:00:00Z',
        updated_at: '2025-01-03T00:00:00Z',
      },
      {
        status: 201,
        headers: { Location: `/api/v1/items/33333333-3333-3333-3333-333333333333` },
      },
    );
  }),

  // PATCH /api/v1/items/:id
  http.patch(`${API_BASE}/items/:id`, async ({ request, params }) => {
    const id = params.id as string;
    const body = (await request.json()) as {
      name?: string;
      description?: string;
      is_active?: boolean;
    };
    return HttpResponse.json({
      id,
      name: body.name ?? 'Alpha Item',
      description: body.description ?? 'First test item',
      is_active: body.is_active ?? true,
      created_at: '2025-01-01T00:00:00Z',
      updated_at: '2025-01-03T00:00:00Z',
    });
  }),

  // DELETE /api/v1/items/:id
  http.delete(`${API_BASE}/items/:id`, ({ params }) => {
    const id = params.id as string;
    if (id === '11111111-1111-1111-1111-111111111111') {
      return new HttpResponse(null, { status: 204 });
    }
    return new HttpResponse(null, { status: 404, statusText: 'Not Found' });
  }),
];
