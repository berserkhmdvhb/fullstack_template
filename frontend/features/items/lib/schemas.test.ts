/**
 * Unit tests for Item Zod schemas.
 *
 * @packageDocumentation
 */

import { describe, expect, it } from 'vitest';

import { itemCreateSchema, itemUpdateSchema } from '@/features/items/lib/schemas';

describe('itemCreateSchema', () => {
  it('should accept valid minimal data', () => {
    const result = itemCreateSchema.safeParse({ name: 'Test' });

    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.name).toBe('Test');
      expect(result.data.description).toBeUndefined();
      expect(result.data.is_active).toBeUndefined();
    }
  });

  it('should accept valid full data', () => {
    const result = itemCreateSchema.safeParse({
      name: 'Test',
      description: 'Description',
      is_active: true,
    });

    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.name).toBe('Test');
      expect(result.data.description).toBe('Description');
      expect(result.data.is_active).toBe(true);
    }
  });

  it('should reject empty name', () => {
    const result = itemCreateSchema.safeParse({ name: '' });

    expect(result.success).toBe(false);
  });

  it('should reject name longer than 255 characters', () => {
    const result = itemCreateSchema.safeParse({ name: 'x'.repeat(256) });

    expect(result.success).toBe(false);
  });

  it('should accept name with exactly 255 characters', () => {
    const result = itemCreateSchema.safeParse({ name: 'x'.repeat(255) });

    expect(result.success).toBe(true);
  });

  it('should reject missing name', () => {
    const result = itemCreateSchema.safeParse({});

    expect(result.success).toBe(false);
  });
});

describe('itemUpdateSchema', () => {
  it('should accept empty update', () => {
    const result = itemUpdateSchema.safeParse({});

    expect(result.success).toBe(true);
  });

  it('should accept partial update', () => {
    const result = itemUpdateSchema.safeParse({ name: 'Updated' });

    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.name).toBe('Updated');
    }
  });

  it('should accept full update', () => {
    const result = itemUpdateSchema.safeParse({
      name: 'Updated',
      description: 'New desc',
      is_active: false,
    });

    expect(result.success).toBe(true);
  });

  it('should reject empty name', () => {
    const result = itemUpdateSchema.safeParse({ name: '' });

    expect(result.success).toBe(false);
  });

  it('should reject name longer than 255 characters', () => {
    const result = itemUpdateSchema.safeParse({ name: 'x'.repeat(256) });

    expect(result.success).toBe(false);
  });
});
