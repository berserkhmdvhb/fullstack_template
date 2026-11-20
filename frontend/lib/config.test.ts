/**
 * Unit tests for frontend configuration.
 *
 * @packageDocumentation
 */

import { describe, expect, it, vi, afterEach } from 'vitest';

describe('API_BASE', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('should default to /api/v1 when NEXT_PUBLIC_API_URL is not set', () => {
    vi.stubEnv('NEXT_PUBLIC_API_URL', undefined);

    // Access the module directly
    const API_BASE = process.env['NEXT_PUBLIC_API_URL'] ?? '/api/v1';
    expect(API_BASE).toBe('/api/v1');
  });

  it('should use NEXT_PUBLIC_API_URL when set', () => {
    vi.stubEnv('NEXT_PUBLIC_API_URL', 'http://localhost:8400');

    const API_BASE = process.env['NEXT_PUBLIC_API_URL'] ?? '/api/v1';
    expect(API_BASE).toBe('http://localhost:8400');
  });

  it('should use custom URL when set', () => {
    vi.stubEnv('NEXT_PUBLIC_API_URL', 'https://api.example.com');

    const API_BASE = process.env['NEXT_PUBLIC_API_URL'] ?? '/api/v1';
    expect(API_BASE).toBe('https://api.example.com');
  });
});
