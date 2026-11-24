/**
 * SAMPLE/REFERENCE: Tests for ItemList component.
 *
 * @remarks
 * Tests rendering, empty state, loading state, search, and delete actions.
 *
 * @packageDocumentation
 */

import userEvent from '@testing-library/user-event';
import { HttpResponse, http } from 'msw/http';
import React from 'react';
import { describe, expect, it, vi } from 'vitest';

import { server } from '@/src/test/mocks/server';
import { renderWithProviders, screen, waitFor, within } from '@/src/test/test-utils';
import { API_PATH } from '@/static-paths';

import { ItemList } from './ItemList';

const noop = (): void => {};

// ---------------------------------------------------------------------------
// Tests: Rendering
// ---------------------------------------------------------------------------

describe('ItemList', () => {
  it('renders the component with heading', async () => {
    renderWithProviders(<ItemList onEdit={noop} />);
    await waitFor(() => {
      expect(screen.getByText(/Items/)).toBeInTheDocument();
    });
  });

  it('renders search input', async () => {
    renderWithProviders(<ItemList onEdit={noop} />);
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
      http.get(`${API_PATH}/items`, () => {
        return HttpResponse.json({ items: [], total: 0 });
      }),
    );

    renderWithProviders(<ItemList onEdit={noop} />);

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
      http.get(`${API_PATH}/items`, () => {
        return new HttpResponse(null, { status: 200 });
      }),
    );

    renderWithProviders(<ItemList onEdit={noop} />);
    // The loading skeleton should appear briefly
    const skeletons = screen.getAllByTestId('skeleton');
    expect(skeletons.length).toBeGreaterThan(0);
  });

  // ---------------------------------------------------------------------------
  // Tests: Data display
  // ---------------------------------------------------------------------------

  it('renders items in a table', async () => {
    renderWithProviders(<ItemList onEdit={noop} />);

    await waitFor(() => {
      expect(screen.getByText('Alpha Item')).toBeInTheDocument();
    });
    expect(screen.getByText('Beta Item')).toBeInTheDocument();
    expect(screen.getByText('First test item')).toBeInTheDocument();
  });

  it('is one structure that adapts: a table with labelled cells and a visually hidden header on phones', async () => {
    renderWithProviders(<ItemList onEdit={noop} />);

    const cell = await screen.findByText('First test item');
    const table = screen.getByRole('table');
    const row = screen.getByRole('row', { name: /Alpha Item/ });
    const cells = within(row).getAllByRole('cell');
    expect(table).toHaveClass('max-md:block');
    expect(screen.getAllByRole('rowgroup')[0]).toHaveClass('max-md:sr-only');
    expect(row).toHaveClass('max-md:block');
    // Each value is labelled for the stacked layout, and the actions live in the same row.
    expect(cell).toHaveAttribute('data-label', 'Description');
    expect(cells[2]).toHaveAttribute('data-label', 'Status');
    expect(cells[3]).toHaveAttribute('data-label', 'Created');
    expect(within(row).getByRole('button', { name: 'Edit Alpha Item' })).toBeInTheDocument();
    expect(within(row).getByRole('button', { name: 'Delete Alpha Item' })).toBeInTheDocument();
    // The status class sits on the badge itself, where globals.css draws the dot (.badge-status.active::before).
    expect(cells[2]).toContainHTML('class="badge-status active"');
    // Explicit roles keep the table semantics where `display: block` would drop them (Safari, Firefox).
    expect(table).toHaveAttribute('role', 'table');
    expect(row).toHaveAttribute('role', 'row');
    expect(cells[0]).toHaveAttribute('role', 'cell');
    expect(screen.getAllByRole('columnheader')[0]).toHaveAttribute('role', 'columnheader');
    // The table is in the accessibility tree once, with its column headers.
    expect(screen.getAllByRole('table')).toHaveLength(1);
    expect(screen.getAllByRole('columnheader')).toHaveLength(5);
  });

  it('marks an inactive item with the inactive status class', async () => {
    renderWithProviders(<ItemList onEdit={noop} />);

    const row = await screen.findByRole('row', { name: /Beta Item/ });

    expect(within(row).getAllByRole('cell')[2]).toContainHTML('class="badge-status inactive"');
  });

  it('shows active/inactive status indicators', async () => {
    renderWithProviders(<ItemList onEdit={noop} />);

    await waitFor(() => {
      expect(screen.getByText('Active')).toBeInTheDocument();
    });
    expect(screen.getByText('Inactive')).toBeInTheDocument();
  });

  it('shows delete button for each item', async () => {
    renderWithProviders(<ItemList onEdit={noop} />);

    await waitFor(() => {
      const deleteButtons = screen.getAllByText('Delete');
      expect(deleteButtons.length).toBe(2);
    });
  });

  // ---------------------------------------------------------------------------
  // Tests: Search
  // ---------------------------------------------------------------------------

  it('filters items when search text is entered', async () => {
    renderWithProviders(<ItemList onEdit={noop} />);

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
      http.get(`${API_PATH}/items`, () => {
        return HttpResponse.json({ detail: 'Internal Server Error' }, { status: 500 });
      }),
    );

    renderWithProviders(<ItemList onEdit={noop} />);

    await waitFor(() => {
      expect(screen.getByText('Failed to load items.')).toBeInTheDocument();
    });
  });

  // ---------------------------------------------------------------------------
  // Tests: Item count
  // ---------------------------------------------------------------------------

  it('displays total item count in heading', async () => {
    renderWithProviders(<ItemList onEdit={noop} />);

    await waitFor(() => {
      expect(screen.getByText(/Items \(2\)/)).toBeInTheDocument();
    });
  });

  // ---------------------------------------------------------------------------
  // Tests: Actions
  // ---------------------------------------------------------------------------

  it('passes the clicked item to onEdit', async () => {
    const onEdit = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(<ItemList onEdit={onEdit} />);

    await user.click(await screen.findByRole('button', { name: 'Edit Beta Item' }));

    expect(onEdit).toHaveBeenCalledWith(expect.objectContaining({ name: 'Beta Item' }));
  });

  it('retries the fetch from the error state', async () => {
    let failing = true;
    server.use(
      http.get(`${API_PATH}/items`, () =>
        failing
          ? HttpResponse.json({ detail: 'down' }, { status: 500 })
          : HttpResponse.json({ items: [], total: 0 }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<ItemList onEdit={noop} />);
    await screen.findByText('Failed to load items.');

    failing = false;
    await user.click(screen.getByRole('button', { name: 'Retry' }));

    expect(await screen.findByText('No items found.')).toBeInTheDocument();
  });

  it('deletes an item after confirmation and says so', async () => {
    const deleted: string[] = [];
    server.use(
      http.delete(`${API_PATH}/items/:id`, ({ params }) => {
        deleted.push(params['id'] as string);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<ItemList onEdit={noop} />);

    await user.click(await screen.findByRole('button', { name: 'Delete Alpha Item' }));
    const dialog = screen.getByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }));

    expect(await screen.findByText('Item deleted successfully.')).toBeInTheDocument();
    expect(deleted).toEqual(['11111111-1111-1111-1111-111111111111']);
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
  });

  it('does not delete when the confirmation is cancelled', async () => {
    let deletes = 0;
    server.use(
      http.delete(`${API_PATH}/items/:id`, () => {
        deletes += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<ItemList onEdit={noop} />);

    await user.click(await screen.findByRole('button', { name: 'Delete Alpha Item' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(deletes).toBe(0);
  });

  it('reports a failed delete and closes the dialog', async () => {
    const user = userEvent.setup();
    renderWithProviders(<ItemList onEdit={noop} />);

    // The default handler answers 404 for every id except Alpha's.
    await user.click(await screen.findByRole('button', { name: 'Delete Beta Item' }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }));

    expect(await screen.findByText(/^Failed to delete item:/)).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  // ---------------------------------------------------------------------------
  // Tests: Pagination
  // ---------------------------------------------------------------------------

  it('pages through the results', async () => {
    const pages: string[] = [];
    server.use(
      http.get(`${API_PATH}/items`, ({ request }) => {
        const page = new URL(request.url).searchParams.get('page') ?? '1';
        pages.push(page);
        return HttpResponse.json({
          items: [
            {
              id: `00000000-0000-0000-0000-00000000000${page}`,
              name: `Item on page ${page}`,
              description: '',
              is_active: true,
              created_at: '2025-01-01T00:00:00Z',
              updated_at: '2025-01-01T00:00:00Z',
            },
          ],
          total: 3,
          total_pages: 3,
        });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<ItemList onEdit={noop} />);

    expect(await screen.findByText('Item on page 1')).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Pagination' })).toBeInTheDocument();
    expect(screen.getByText('Page 1 of 3 (3 items)')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled();

    await user.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText('Item on page 2')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText('Item on page 3')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();

    await user.click(screen.getByRole('button', { name: 'Previous' }));
    expect(await screen.findByText('Item on page 2')).toBeInTheDocument();
    expect(pages).toContain('3');
  });

  it('hides the pager when everything fits on one page', async () => {
    renderWithProviders(<ItemList onEdit={noop} />);

    await screen.findByText('Alpha Item');

    expect(screen.queryByRole('navigation', { name: 'Pagination' })).not.toBeInTheDocument();
  });
});
