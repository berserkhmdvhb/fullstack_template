/**
 * SAMPLE/REFERENCE: Typed API functions for Item CRUD.
 *
 * @remarks
 * Uses the existing `httpJSON` client and auto-generated types from `types.gen.d.ts`.
 *
 * @packageDocumentation
 */

import { httpJSON } from '@/lib/api/client';
import type { paths } from '@/lib/api/types.gen';
import { API_BASE } from '@/lib/config';

type ItemResponse =
  paths['/api/v1/items/{item_id}']['get']['responses']['200']['content']['application/json'];
type ItemListResponse =
  paths['/api/v1/items']['get']['responses']['200']['content']['application/json'];
type ItemCreatePayload =
  paths['/api/v1/items']['post']['requestBody']['content']['application/json'];
type ItemUpdatePayload =
  paths['/api/v1/items/{item_id}']['patch']['requestBody']['content']['application/json'];

/**
 * Fetch all items with optional search filter and pagination.
 *
 * @param search - Optional search string (ilike on name/description).
 * @param page - Page number (1-indexed, default 1).
 * @param pageSize - Items per page (1-100, default 50).
 * @returns List response with items array and pagination metadata.
 *
 * @public
 */
export async function getItems(
  search?: string,
  page: number = 1,
  pageSize: number = 50,
): Promise<ItemListResponse> {
  const params = new URLSearchParams();
  if (search) params.set('search', search);
  params.set('page', page.toString());
  params.set('page_size', pageSize.toString());
  return httpJSON<ItemListResponse>(`${API_BASE}/items?${params.toString()}`, {
    timeoutMs: 10_000,
  });
}

/**
 * Fetch a single item by ID.
 *
 * @param id - Item UUID.
 * @returns The item response.
 *
 * @public
 */
export async function getItem(id: string): Promise<ItemResponse> {
  return httpJSON<ItemResponse>(`${API_BASE}/items/${id}`, { timeoutMs: 10_000 });
}

/**
 * Create a new item.
 *
 * @param data - ItemCreate payload.
 * @returns The created item response.
 *
 * @public
 */
export async function createItem(data: ItemCreatePayload): Promise<ItemResponse> {
  return httpJSON<ItemResponse>(`${API_BASE}/items`, {
    method: 'POST',
    body: JSON.stringify(data),
    timeoutMs: 10_000,
  });
}

/**
 * Partially update an item.
 *
 * @param id - Item UUID.
 * @param data - ItemUpdate payload (all fields optional).
 * @returns The updated item response.
 *
 * @public
 */
export async function updateItem(id: string, data: ItemUpdatePayload): Promise<ItemResponse> {
  return httpJSON<ItemResponse>(`${API_BASE}/items/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
    timeoutMs: 10_000,
  });
}

/**
 * Delete an item.
 *
 * @param id - Item UUID.
 *
 * @public
 */
export async function deleteItem(id: string): Promise<void> {
  await httpJSON<void>(`${API_BASE}/items/${id}`, { method: 'DELETE', timeoutMs: 10_000 });
}
