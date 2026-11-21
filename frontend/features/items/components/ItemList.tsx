/**
 * SAMPLE/REFERENCE: Item list component with search, pagination, and actions.
 *
 * @remarks
 * Demonstrates React Query integration, loading states, pagination, and user actions.
 * Uses ConfirmModal instead of window.confirm() for delete confirmation.
 *
 * @packageDocumentation
 */

'use client';

import { Trash2, Pencil, Search, ClipboardList } from 'lucide-react';
import React, { useEffect, useState, type JSX } from 'react';

import { ConfirmModal } from '@/components/ConfirmModal';
import { mapApiError } from '@/lib/api/error-map';
import type { components } from '@/lib/api/types.gen';
import { useItems, useDeleteItem } from '@/lib/hooks/useItems';
import { useToast } from '@/lib/toast';

/** A table cell; below md it becomes a block inside a stacked card. */
const CELL = 'table-cell max-md:block max-md:px-0 max-md:py-1';
/** Below md a cell shows its column name in front of the value (the header row is visually hidden there). */
const LABELLED =
  'max-md:before:mr-2 max-md:before:inline-block max-md:before:min-w-24 max-md:before:font-medium max-md:before:text-textSecondary max-md:before:content-[attr(data-label)]';

/**
 * Explicit table roles. Below md the table elements are `display: block`, and Safari and Firefox can drop their
 * table semantics then; the roles keep them in every engine. (Spread, because the jsx-a11y rule against redundant
 * roles cannot tell that the CSS changes what the element exposes.)
 */
const ROLE = {
  table: { role: 'table' },
  rowgroup: { role: 'rowgroup' },
  row: { role: 'row' },
  columnheader: { role: 'columnheader' },
  cell: { role: 'cell' },
} as const;

type ItemResponse = components['schemas']['ItemResponse'];

/**
 * Props for the ItemList component.
 *
 * @public
 */
type ItemListProps = {
  /** Callback fired when user clicks Edit on a row, passing the item. */
  onEdit: (item: ItemResponse) => void;
};

/**
 * ItemList component.
 *
 * @remarks
 * - Search input for filtering items
 * - Loading skeleton while fetching
 * - Data table with name, description, status, and actions
 * - Pagination controls
 * - Empty state message when no items exist
 * - Delete confirmation via modal (not window.confirm)
 * - Edit button per row to trigger inline editing
 *
 * @param onEdit - Callback fired when user clicks Edit on a row, passing the item.
 *
 * @returns The item list view.
 *
 * @public
 */
export function ItemList({ onEdit }: ItemListProps): JSX.Element {
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize] = useState(20);
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const { notify } = useToast();

  const { data, isLoading, error, refetch } = useItems(search, page, pageSize);
  const deleteMutation = useDeleteItem();

  // Debounce search input (300ms)
  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(searchInput);
      setPage(1); // Reset to first page on search change
    }, 300);
    return () => clearTimeout(timer);
  }, [searchInput]);

  const handleSearch = (e: React.ChangeEvent<HTMLInputElement>) => {
    setSearchInput(e.target.value);
  };

  const handleDelete = (id: string) => {
    setDeleteId(id);
  };

  const confirmDelete = () => {
    if (deleteId) {
      deleteMutation.mutate(deleteId, {
        onSuccess: () => {
          notify('success', 'Item deleted successfully.');
          setDeleteId(null);
        },
        onError: (err) => {
          const { message } = mapApiError(err);
          notify('error', `Failed to delete item: ${message}`);
          setDeleteId(null);
        },
      });
    }
  };

  const cancelDelete = () => {
    setDeleteId(null);
  };

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = data?.total_pages ?? 0;

  if (isLoading) {
    return (
      <div className="panel space-y-3" data-testid="skeleton">
        <div className="animate-pulse space-y-2">
          <div className="skeleton h-4 w-1/3" />
          <div className="skeleton h-4 w-2/3" />
          <div className="skeleton h-4 w-1/2" />
        </div>
      </div>
    );
  }

  if (error) {
    const { message } = mapApiError(error);
    return (
      <div className="panel space-y-3 text-center">
        <p className="text-textDanger text-sm font-medium">Failed to load items.</p>
        <p className="text-xs text-textPrimary">{message}</p>
        <button type="button" onClick={() => void refetch()} className="btn-neutral btn-sm">
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="panel space-y-4">
      <header className="flex items-center justify-between gap-4">
        <h2 className="section-title">Items ({total})</h2>
        <div className="relative flex-1 max-w-xs">
          <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-textPrimary" />
          <input
            type="text"
            placeholder="Search items..."
            value={searchInput}
            onChange={handleSearch}
            className="input w-full"
            style={{ paddingLeft: '2.75rem' }}
            aria-label="Search items"
          />
        </div>
      </header>

      {items.length === 0 ? (
        <div className="text-center py-12 text-textPrimary">
          <ClipboardList className="h-12 w-12 mx-auto mb-3 opacity-60" />
          <p className="text-sm font-medium text-textPrimary">No items found.</p>
          <p className="text-xs mt-1 text-textPrimary">Create your first item to get started.</p>
        </div>
      ) : (
        <>
          {/* One structure for every width. Below md (768 px) each row is a stacked card (labelled fields, the actions in
              view, no sideways scroll); the table fits from about 640 px, and thead stays for assistive tech. */}
          <div className="overflow-x-auto">
            <table className="w-full text-sm max-md:block" {...ROLE.table}>
              <thead className="max-md:sr-only" {...ROLE.rowgroup}>
                <tr className="border-b border-borderWeak" {...ROLE.row}>
                  <th
                    className="text-left py-3 px-4 font-medium text-textPrimary"
                    {...ROLE.columnheader}
                  >
                    Name
                  </th>
                  <th
                    className="text-left py-3 px-4 font-medium text-textPrimary"
                    {...ROLE.columnheader}
                  >
                    Description
                  </th>
                  <th
                    className="text-left py-3 px-4 font-medium text-textPrimary"
                    {...ROLE.columnheader}
                  >
                    Status
                  </th>
                  <th
                    className="text-left py-3 px-4 font-medium text-textPrimary"
                    {...ROLE.columnheader}
                  >
                    Created
                  </th>
                  <th
                    className="text-right py-3 px-4 font-medium text-textPrimary"
                    {...ROLE.columnheader}
                  >
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="max-md:block" {...ROLE.rowgroup}>
                {items.map((item) => (
                  <tr
                    key={item.id}
                    {...ROLE.row}
                    className="border-b border-borderWeak table-row-hover max-md:block max-md:py-3"
                  >
                    <td className={`${CELL} text-textPrimary font-medium`} {...ROLE.cell}>
                      {item.name}
                    </td>
                    <td
                      className={`${CELL} ${LABELLED} text-textPrimary`}
                      data-label="Description"
                      {...ROLE.cell}
                    >
                      {item.description || '—'}
                    </td>
                    <td className={`${CELL} ${LABELLED}`} data-label="Status" {...ROLE.cell}>
                      {/* The class sits on the badge itself: globals.css draws the dot with .badge-status.active::before */}
                      <span className={`badge-status ${item.is_active ? 'active' : 'inactive'}`}>
                        <span className="text-textPrimary">
                          {item.is_active ? 'Active' : 'Inactive'}
                        </span>
                      </span>
                    </td>
                    <td
                      className={`${CELL} ${LABELLED} text-textPrimary text-sm md:text-xs`}
                      data-label="Created"
                    >
                      {new Date(item.created_at).toLocaleDateString()}
                    </td>
                    <td className={`${CELL} text-right max-md:text-left`} {...ROLE.cell}>
                      <div className="inline-flex items-center gap-1">
                        <button
                          onClick={() => onEdit(item)}
                          disabled={deleteMutation.isPending}
                          aria-label={`Edit ${item.name}`}
                          className="btn-ghost btn-sm inline-flex items-center gap-1.5"
                        >
                          <Pencil className="h-3.5 w-3.5" />
                          Edit
                        </button>
                        <button
                          onClick={() => handleDelete(item.id)}
                          disabled={deleteMutation.isPending}
                          aria-label={`Delete ${item.name}`}
                          className="btn-ghost btn-sm btn-danger inline-flex items-center gap-1.5"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                          {deleteMutation.isPending ? 'Deleting...' : 'Delete'}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <nav className="flex items-center justify-between" aria-label="Pagination">
              <p className="text-sm text-textPrimary">
                Page {page} of {totalPages} ({total} items)
              </p>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page === 1}
                  className="btn-neutral btn-sm disabled:opacity-50"
                >
                  Previous
                </button>
                <button
                  type="button"
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  disabled={page === totalPages}
                  className="btn-neutral btn-sm disabled:opacity-50"
                >
                  Next
                </button>
              </div>
            </nav>
          )}
        </>
      )}

      {/* Delete confirmation modal */}
      <ConfirmModal
        isOpen={deleteId !== null}
        title="Delete Item"
        message="Are you sure you want to delete this item? This action cannot be undone."
        confirmLabel="Delete"
        onConfirm={confirmDelete}
        onCancel={cancelDelete}
      />
    </div>
  );
}
