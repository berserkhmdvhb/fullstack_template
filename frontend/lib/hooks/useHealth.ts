/**
 * SAMPLE/REFERENCE: React Query hook for backend health.
 *
 * @remarks
 * Thin wrapper over the typed {@link getHealth} endpoint function, following the same
 * React Query v5 patterns as the Item hooks (`./useItems`). Use it on dashboards or
 * status pages to poll the `/health` probe declaratively instead of hand-rolling a
 * `useEffect` fetch.
 *
 * @packageDocumentation
 */

'use client';

import { useQuery } from '@tanstack/react-query';

import { getHealth } from '@/lib/api/endpoints';
import type { components } from '@/lib/api/types.gen';

type HealthStatus = components['schemas']['HealthStatus'];

/**
 * Query the backend `/health` probe.
 *
 * @returns React Query result; `data.status` is `"ok"` when the backend is healthy.
 *
 * @public
 */
export function useHealth(): ReturnType<typeof useQuery<HealthStatus>> {
  return useQuery<HealthStatus>({
    queryKey: ['health'],
    queryFn: () => getHealth(),
    retry: false,
  });
}
