/**
 * Items page module.
 *
 * @remarks
 * CRUD interface for the sample Item entity using composed components.
 * Manages editing state and coordinates between ItemList and ItemForm.
 *
 * @packageDocumentation
 */

'use client';

import React, { useState } from 'react';

import { ItemForm } from '@/features/items/components/ItemForm';
import { ItemList } from '@/features/items/components/ItemList';
import type { components } from '@/lib/api/types.gen';

type ItemResponse = components['schemas']['ItemResponse'];

/**
 * Items page component.
 *
 * @remarks
 * Renders the item list and create/edit form in a responsive side-by-side layout.
 * Manages the editing state: when a user clicks Edit on a row, the form switches
 * to edit mode with pre-populated fields.
 *
 * @returns The items page view.
 *
 * @public
 */
export default function ItemsPage(): JSX.Element {
  const [editingItem, setEditingItem] = useState<ItemResponse | null>(null);

  const handleEdit = (item: ItemResponse) => {
    setEditingItem(item);
  };

  const handleCancelEdit = () => {
    setEditingItem(null);
  };

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-textPrimary">Items</h1>
        <p className="mt-1 text-textPrimary">
          Manage items with full CRUD operations (PostgreSQL-backed).
        </p>
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2">
          <ItemList onEdit={handleEdit} />
        </div>
        <div>
          <ItemForm editingItem={editingItem} onCancelEdit={handleCancelEdit} />
        </div>
      </div>
    </div>
  );
}
