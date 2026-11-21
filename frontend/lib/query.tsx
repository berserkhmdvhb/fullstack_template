/**
 * React Query provider and client factory with env-driven configuration.
 *
 * @remarks
 * Creates a strictly configured `QueryClient` and provides it via
 * `QueryClientProvider` to the App Router subtree.
 *
 * Query defaults (retry, staleTime, gcTime, refetchOnWindowFocus) are
 * configurable via `NEXT_PUBLIC_QUERY_*` environment variables.
 *
 * @packageDocumentation
 */

'use client';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React, { useState, type PropsWithChildren, type JSX } from 'react';

/**
 * Read an integer env var with a fallback default.
 *
 * @param name - The environment variable name.
 * @param defaultValue - The default value if the env var is missing or invalid.
 * @returns The parsed integer or the default.
 *
 * @internal
 */
function envInt(name: string, defaultValue: number): number {
  const raw = typeof process !== 'undefined' ? process.env?.[name] : undefined;
  if (raw === undefined || raw === '') return defaultValue;
  const parsed = parseInt(raw, 10);
  return Number.isFinite(parsed) ? parsed : defaultValue;
}

/**
 * Read a boolean env var with a fallback default.
 *
 * @param name - The environment variable name.
 * @param defaultValue - The default value if the env var is missing.
 * @returns The parsed boolean or the default.
 *
 * @internal
 */
function envBool(name: string, defaultValue: boolean): boolean {
  const raw = typeof process !== 'undefined' ? process.env?.[name] : undefined;
  if (raw === undefined || raw === '') return defaultValue;
  return raw.toLowerCase() === 'true';
}

/**
 * Creates a configured QueryClient using env-driven or sensible defaults.
 *
 * @remarks
 * Reads the following environment variables:
 * - `NEXT_PUBLIC_QUERY_RETRY` (default: `1`)
 * - `NEXT_PUBLIC_QUERY_STALE_TIME` (default: `30000`)
 * - `NEXT_PUBLIC_QUERY_GC_TIME` (default: `300000`)
 * - `NEXT_PUBLIC_QUERY_REFETCH_ON_FOCUS` (default: `false`)
 *
 * @returns A configured QueryClient instance.
 *
 * @internal
 */
function createClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: envInt('NEXT_PUBLIC_QUERY_RETRY', 1),
        staleTime: envInt('NEXT_PUBLIC_QUERY_STALE_TIME', 30_000),
        gcTime: envInt('NEXT_PUBLIC_QUERY_GC_TIME', 300_000),
        refetchOnWindowFocus: envBool('NEXT_PUBLIC_QUERY_REFETCH_ON_FOCUS', false),
      },
      mutations: { retry: 0 },
    },
  });
}

/**
 * Provides a TanStack Query Client to all children.
 *
 * @param props - Child components requiring Query access.
 * @returns The provider-wrapped subtree.
 *
 * @public
 */
export function QueryProvider({ children }: PropsWithChildren): JSX.Element {
  const [client] = useState<QueryClient>(() => createClient());
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
