/**
 * Barrel export for the API module.
 *
 * @public
 */

export { httpJSON, HttpError } from './client';
export { mapApiError } from './error-map';
export type { MappedApiError } from './error-map';

// Canonical Item CRUD client (SAMPLE/REFERENCE). Re-exported here so consumers have
// one obvious import path — `@/lib/api` — instead of reaching into `./items` directly.
export { createItem, deleteItem, getItem, getItems, updateItem } from './items';
