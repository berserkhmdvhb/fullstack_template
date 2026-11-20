/**
 * SAMPLE/REFERENCE: Zod validation schemas for Item forms.
 *
 * @remarks
 * Mirrors backend Pydantic constraints: name 1-255 chars, description optional, is_active boolean.
 *
 * @packageDocumentation
 */

import { z } from 'zod';

/**
 * Zod schema for creating an item.
 *
 * @remarks
 * - `name`: required, 1-255 characters
 * - `description`: optional string
 * - `is_active`: optional boolean
 */
export const itemCreateSchema = z.object({
  name: z.string().min(1, 'Name is required').max(255, 'Name must be 255 characters or less'),
  description: z.string().optional(),
  is_active: z.boolean().optional(),
});

/**
 * Zod schema for updating an item.
 *
 * @remarks
 * All fields optional for partial updates.
 */
export const itemUpdateSchema = z.object({
  name: z
    .string()
    .min(1, 'Name must be at least 1 character')
    .max(255, 'Name must be 255 characters or less')
    .optional(),
  description: z.string().optional(),
  is_active: z.boolean().optional(),
});

/**
 * Inferred TypeScript type for item creation.
 *
 * @public
 */
export type ItemCreateInput = z.infer<typeof itemCreateSchema>;

/**
 * Inferred TypeScript type for item update.
 *
 * @public
 */
export type ItemUpdateInput = z.infer<typeof itemUpdateSchema>;
