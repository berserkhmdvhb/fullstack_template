/**
 * SAMPLE/REFERENCE: React Query hooks for Item CRUD.
 *
 * @remarks
 * Follows React Query v5 patterns with query keys as arrays and mutation invalidation.
 *
 * @packageDocumentation
 */

'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { createItem, deleteItem, getItem, getItems, updateItem } from '@/lib/api/items';
import type { paths } from '@/lib/api/types.gen';

type ItemUpdatePayload =
  paths['/api/v1/items/{item_id}']['patch']['requestBody']['content']['application/json'];

/**
 * Hook to fetch all items with optional search and pagination.
 *
 * @param search - Optional search string.
 * @param page - Page number (1-indexed, default 1).
 * @param pageSize - Items per page (default 50).
 * @returns Query result with items list and pagination metadata.
 *
 * @public
 */
export function useItems(search?: string, page: number = 1, pageSize: number = 50) {
  return useQuery({
    queryKey: ['items', search ?? '', page, pageSize],
    queryFn: () => getItems(search, page, pageSize),
    retry: false,
  });
}

/**
 * Hook to fetch a single item by ID.
 *
 * @param id - Item UUID.
 * @returns Query result with single item.
 *
 * @public
 */
export function useItem(id: string) {
  return useQuery({
    queryKey: ['item', id],
    queryFn: () => getItem(id),
    enabled: !!id,
    retry: false,
  });
}

/**
 * Hook to create a new item.
 *
 * @returns Mutation object with mutate function.
 *
 * @public
 */
export function useCreateItem() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: createItem,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['items'] });
    },
  });
}

/**
 * Hook to update an item.
 *
 * @returns Mutation object with mutate function.
 *
 * @public
 */
export function useUpdateItem() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: ItemUpdatePayload }) => updateItem(id, data),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ['items'] });
      void queryClient.invalidateQueries({ queryKey: ['item', variables.id] });
    },
  });
}

/**
 * Hook to delete an item.
 *
 * @returns Mutation object with mutate function.
 *
 * @public
 */
export function useDeleteItem() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: deleteItem,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['items'] });
    },
  });
}
