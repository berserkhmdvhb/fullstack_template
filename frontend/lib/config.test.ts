/**
 * Unit tests for frontend configuration.
 *
 * @packageDocumentation
 */

import { describe, expect, it, vi, afterEach } from 'vitest';

import { API_PATH } from '@/static-paths';

/** `lib/config.ts` reads the env once, at import: load a fresh copy after stubbing it. */
async function apiBase(): Promise<string> {
  vi.resetModules();
  return (await import('./config')).API_BASE;
}

describe('API_BASE', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('should default to the same-origin API_PATH when NEXT_PUBLIC_API_URL is not set', async () => {
    vi.stubEnv('NEXT_PUBLIC_API_URL', undefined);

    expect(await apiBase()).toBe(API_PATH);
  });

  it('should use NEXT_PUBLIC_API_URL when set', async () => {
    vi.stubEnv('NEXT_PUBLIC_API_URL', 'http://localhost:8400');

    expect(await apiBase()).toBe('http://localhost:8400');
  });

  it('should use custom URL when set', async () => {
    vi.stubEnv('NEXT_PUBLIC_API_URL', 'https://api.example.com');

    expect(await apiBase()).toBe('https://api.example.com');
  });
});
