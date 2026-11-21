/**
 * Browser-side answers for the API calls the pages make, so a spec renders the same with or without a backend.
 *
 * @packageDocumentation
 */

import type { Page } from '@playwright/test';

export const ITEMS = {
  items: [
    {
      id: '11111111-1111-1111-1111-111111111111',
      name: 'First item',
      description: 'A described item',
      is_active: true,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    },
    {
      id: '22222222-2222-2222-2222-222222222222',
      name: 'Second item',
      description: null,
      is_active: false,
      created_at: '2026-01-02T00:00:00Z',
      updated_at: '2026-01-02T00:00:00Z',
    },
  ],
  total: 2,
  page: 1,
  page_size: 20,
  total_pages: 1,
};

/** Answer `/api/v1/health` and the items list, or make either one fail like an unreachable backend. */
export async function mockApi(
  page: Page,
  state: { health?: 'up' | 'down'; items?: 'up' | 'down' } = {},
): Promise<void> {
  await page.route('**/api/v1/health', (route) =>
    state.health === 'down' ? route.abort() : route.fulfill({ json: { status: 'ok' } }),
  );
  await page.route('**/api/v1/items?*', (route) =>
    state.items === 'down' ? route.abort() : route.fulfill({ json: ITEMS }),
  );
}
