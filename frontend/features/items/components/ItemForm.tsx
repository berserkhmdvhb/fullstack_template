/**
 * Item create/edit form with Zod validation.
 *
 * @remarks
 * Uses react-hook-form + zod resolver for form validation matching backend constraints.
 * Supports both create and edit modes via the optional `editingItem` prop.
 *
 * @packageDocumentation
 */

'use client';

import { zodResolver } from '@hookform/resolvers/zod';
import { Pencil, Plus, X } from 'lucide-react';
import React, { useEffect, type JSX } from 'react';
import { useForm } from 'react-hook-form';

import {
  itemCreateSchema,
  itemUpdateSchema,
  type ItemCreateInput,
  type ItemUpdateInput,
} from '@/features/items/lib/schemas';
import { mapApiError } from '@/lib/api/error-map';
import type { components } from '@/lib/api/types.gen';
import { useCreateItem, useUpdateItem } from '@/lib/hooks/useItems';
import { useToast } from '@/lib/toast';

type ItemResponse = components['schemas']['ItemResponse'];

/**
 * Props for the ItemForm component.
 *
 * @public
 */
type ItemFormProps = {
  /** When provided, the form switches to edit mode and pre-populates fields. */
  editingItem?: ItemResponse | null;
  /** Callback to clear the editing state (e.g., cancel edit). */
  onCancelEdit?: () => void;
};

/**
 * ItemForm component.
 *
 * @remarks
 * - Name field (required, 1-255 chars)
 * - Description field (optional)
 * - Is active checkbox (default true)
 * - Zod validation matching backend Pydantic constraints
 * - Success/error feedback via toast
 * - Edit mode: pre-populates fields, uses PATCH endpoint, shows cancel button
 *
 * @returns The item create/edit form.
 *
 * @public
 */
export function ItemForm({ editingItem, onCancelEdit }: ItemFormProps): JSX.Element {
  const createMutation = useCreateItem();
  const updateMutation = useUpdateItem();
  const { notify } = useToast();

  const isEditing = !!editingItem;

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<ItemCreateInput | ItemUpdateInput>({
    resolver: zodResolver(isEditing ? itemUpdateSchema : itemCreateSchema),
    defaultValues: isEditing
      ? {
          name: editingItem.name,
          description: editingItem.description || undefined,
          is_active: editingItem.is_active,
        }
      : {
          name: '',
          description: undefined,
          is_active: undefined,
        },
  });

  // Re-populate form when editingItem changes
  useEffect(() => {
    if (editingItem) {
      reset({
        name: editingItem.name,
        description: editingItem.description || undefined,
        is_active: editingItem.is_active,
      });
    } else {
      reset({
        name: '',
        description: undefined,
        is_active: undefined,
      });
    }
  }, [editingItem, reset]);

  const onSubmit = async (data: ItemCreateInput | ItemUpdateInput) => {
    if (isEditing && editingItem) {
      // Convert undefined to null for the update API
      const updateData = {
        name: data.name ?? null,
        description: data.description ?? null,
        is_active: data.is_active ?? null,
      };
      updateMutation.mutate(
        {
          id: editingItem.id,
          data: updateData,
        },
        {
          onSuccess: () => {
            notify('success', 'Item updated successfully.');
            onCancelEdit?.();
          },
          onError: (err) => {
            const { message } = mapApiError(err);
            notify('error', `Failed to update item: ${message}`);
          },
        },
      );
    } else {
      // In create mode, data is ItemCreateInput where name is required
      const createData = data as ItemCreateInput;
      createMutation.mutate(
        {
          name: createData.name,
          description: createData.description ?? '',
          is_active: createData.is_active ?? true,
        },
        {
          onSuccess: () => {
            notify('success', 'Item created successfully.');
            reset();
          },
          onError: (err) => {
            const { message } = mapApiError(err);
            notify('error', `Failed to create item: ${message}`);
          },
        },
      );
    }
  };

  const isPending = isSubmitting || createMutation.isPending || updateMutation.isPending;

  return (
    <form
      onSubmit={(e) => {
        void handleSubmit(onSubmit)(e);
      }}
      className="panel space-y-5"
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {isEditing ? (
            <>
              <Pencil className="h-5 w-5 text-accentOrange" />
              <h2 className="section-title">Edit Item</h2>
            </>
          ) : (
            <>
              <Plus className="h-5 w-5 text-accentBlue" />
              <h2 className="section-title">Create Item</h2>
            </>
          )}
        </div>
        {isEditing && (
          <button
            type="button"
            onClick={onCancelEdit}
            disabled={isPending}
            className="btn-ghost btn-sm text-textPrimary"
            aria-label="Cancel editing"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>

      <div className="space-y-4">
        <div className="space-y-1.5">
          <label htmlFor="name" className="block text-sm font-medium text-textPrimary">
            Name <span className="text-statusFailed">*</span>
          </label>
          <input
            id="name"
            type="text"
            {...register('name', { required: 'Name is required' })}
            className="input w-full"
            placeholder={isEditing ? 'Update item name' : 'Enter item name'}
            aria-invalid={errors.name ? 'true' : undefined}
            aria-describedby={errors.name ? 'name-error' : undefined}
          />
          {errors.name && (
            <p id="name-error" className="text-xs text-statusFailed" role="alert">
              {errors.name.message}
            </p>
          )}
        </div>

        <div className="space-y-1.5">
          <label htmlFor="description" className="block text-sm font-medium text-textPrimary">
            Description
          </label>
          <textarea
            id="description"
            {...register('description')}
            className="input w-full resize-none"
            rows={3}
            placeholder={isEditing ? 'Update description' : 'Optional description'}
          />
          {errors.description && (
            <p className="text-xs text-statusFailed" role="alert">
              {errors.description.message}
            </p>
          )}
        </div>

        <div className="flex items-center gap-2.5">
          <input
            id="is_active"
            type="checkbox"
            {...register('is_active')}
            className="h-4 w-4 rounded border-borderWeak accent-accentBlue"
          />
          <label htmlFor="is_active" className="text-sm text-textPrimary">
            Active
          </label>
        </div>
      </div>

      <div className="flex gap-2">
        <button
          type="submit"
          disabled={isPending}
          className="btn-primary flex-1 py-2.5 disabled:opacity-50"
        >
          {isPending ? (
            <span className="flex items-center justify-center gap-2">
              <span className="spinner-xs" />
              {isEditing ? 'Updating...' : 'Creating...'}
            </span>
          ) : isEditing ? (
            'Update Item'
          ) : (
            'Create Item'
          )}
        </button>
        {isEditing && (
          <button
            type="button"
            onClick={onCancelEdit}
            disabled={isPending}
            className="btn-neutral py-2.5 disabled:opacity-50"
          >
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}
