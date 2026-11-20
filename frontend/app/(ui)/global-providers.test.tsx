/**
 * Tests for global providers wrapper.
 *
 * @packageDocumentation
 */

import { useQuery } from '@tanstack/react-query';
import React from 'react';
import { describe, expect, it } from 'vitest';

import { renderWithProviders, screen, waitFor } from '@/src/test/test-utils';

import { GlobalProviders } from './global-providers';

describe('GlobalProviders', () => {
  it('should provide query client to children', async () => {
    /** Test component that uses useQuery to fetch test data. */
    function TestComponent() {
      const { data, isLoading } = useQuery({
        queryKey: ['test'],
        queryFn: async () => 'test data',
      });

      if (isLoading) return <div>Loading...</div>;
      return <div>{data}</div>;
    }

    renderWithProviders(
      <GlobalProviders>
        <TestComponent />
      </GlobalProviders>,
    );

    await waitFor(() => {
      expect(screen.getByText('test data')).toBeInTheDocument();
    });
  });
});
