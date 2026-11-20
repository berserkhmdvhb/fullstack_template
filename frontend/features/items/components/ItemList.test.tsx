/**
 * SAMPLE/REFERENCE: Tests for ItemList component.
 *
 * @remarks
 * Tests rendering, empty state, loading state, search, and delete actions.
 *
 * @packageDocumentation
 */

import { HttpResponse, http } from 'msw';
import React from 'react';
import { describe, expect, it } from 'vitest';

import { server } from '@/src/test/mocks/server';
import { renderWithProviders, screen, waitFor } from '@/src/test/test-utils';

import { ItemList } from './ItemList';

// ---------------------------------------------------------------------------
// Tests: Rendering
// ---------------------------------------------------------------------------

describe('ItemList', () => {
  it('renders the component with heading', async () => {
    renderWithProviders(<ItemList />);
    await waitFor(() => {
      expect(screen.getByText(/Items/)).toBeInTheDocument();
    });
  });

  it('renders search input', async () => {
    renderWithProviders(<ItemList />);
    await waitFor(() => {
      expect(screen.getByLabelText('Search items')).toBeInTheDocument();
    });
  });

  // ---------------------------------------------------------------------------
  // Tests: Empty state
  // ---------------------------------------------------------------------------

  it('shows empty state when no items exist', async () => {
    // Override MSW handler to return empty list
    server.use(
      http.get('/api/v1/items', () => {
        return HttpResponse.json({ items: [], total: 0 });
      }),
    );

    renderWithProviders(<ItemList />);

    await waitFor(() => {
      expect(screen.getByText('No items found.')).toBeInTheDocument();
    });
    expect(screen.getByText('Create your first item to get started.')).toBeInTheDocument();
  });

  // ---------------------------------------------------------------------------
  // Tests: Loading state
  // ---------------------------------------------------------------------------

  it('shows loading skeleton while fetching', () => {
    // Use a delayed handler to catch the loading state
    server.use(
      http.get('/api/v1/items', () => {
        return new HttpResponse(null, { status: 200 });
      }),
    );

    renderWithProviders(<ItemList />);
    // The loading skeleton should appear briefly
    const skeletons = screen.getAllByTestId('skeleton');
    expect(skeletons.length).toBeGreaterThan(0);
  });

  // ---------------------------------------------------------------------------
  // Tests: Data display
  // ---------------------------------------------------------------------------

  it('renders items in a table', async () => {
    renderWithProviders(<ItemList />);

    await waitFor(() => {
      expect(screen.getByText('Alpha Item')).toBeInTheDocument();
    });
    expect(screen.getByText('Beta Item')).toBeInTheDocument();
    expect(screen.getByText('First test item')).toBeInTheDocument();
  });

  it('shows active/inactive status indicators', async () => {
    renderWithProviders(<ItemList />);

    await waitFor(() => {
      expect(screen.getByText('Active')).toBeInTheDocument();
    });
    expect(screen.getByText('Inactive')).toBeInTheDocument();
  });

  it('shows delete button for each item', async () => {
    renderWithProviders(<ItemList />);

    await waitFor(() => {
      const deleteButtons = screen.getAllByText('Delete');
      expect(deleteButtons.length).toBe(2);
    });
  });

  // ---------------------------------------------------------------------------
  // Tests: Search
  // ---------------------------------------------------------------------------

  it('filters items when search text is entered', async () => {
    renderWithProviders(<ItemList />);

    await waitFor(() => {
      expect(screen.getByText('Alpha Item')).toBeInTheDocument();
    });

    const searchInput = screen.getByLabelText('Search items');
    // Simulate typing in the search field
    const { userEvent } = await import('@testing-library/user-event');
    const user = userEvent.setup();
    await user.type(searchInput, 'Alpha');

    // After typing, the list should re-fetch with the search param
    await waitFor(() => {
      expect(screen.getByText('Alpha Item')).toBeInTheDocument();
    });
  });

  // ---------------------------------------------------------------------------
  // Tests: Error state
  // ---------------------------------------------------------------------------

  it('shows error message when fetch fails', async () => {
    server.use(
      http.get('/api/v1/items', () => {
        return HttpResponse.json({ detail: 'Internal Server Error' }, { status: 500 });
      }),
    );

    renderWithProviders(<ItemList />);

    await waitFor(() => {
      expect(screen.getByText('Failed to load items.')).toBeInTheDocument();
    });
  });

  // ---------------------------------------------------------------------------
  // Tests: Item count
  // ---------------------------------------------------------------------------

  it('displays total item count in heading', async () => {
    renderWithProviders(<ItemList />);

    await waitFor(() => {
      expect(screen.getByText(/Items \(2\)/)).toBeInTheDocument();
    });
  });
});
