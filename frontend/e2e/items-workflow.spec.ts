/**
 * E2E tests for Items CRUD workflow.
 *
 * @remarks
 * Tests the full user workflow for creating, viewing, and deleting items
 * through the frontend UI.
 *
 * @packageDocumentation
 */

import { expect, test } from '@playwright/test';

import { API_PATH as API_BASE } from '../static-paths';

// Clean up all items before running tests to ensure a clean state
test.beforeAll(async ({ request }) => {
  const response = await request.get(`${API_BASE}/items`);
  const data = await response.json();
  const items = data.items || [];

  // Delete all existing items
  for (const item of items) {
    await request.delete(`${API_BASE}/items/${item.id}`);
  }
});

test.describe('Items Page', () => {
  test('should load items page', async ({ page }) => {
    await page.goto('/items');
    await page.waitForLoadState('domcontentloaded');

    await expect(page.locator('h1')).toHaveText('Items');
  });

  test('should display empty state when no items', async ({ page }) => {
    await page.goto('/items');
    await page.waitForLoadState('domcontentloaded');

    // Should show empty state message (with period)
    await expect(page.locator('text=No items found.')).toBeVisible();
  });
});

test.describe('Create Item', () => {
  test('should create a new item', async ({ page }) => {
    await page.goto('/items');
    await page.waitForLoadState('domcontentloaded');

    // Wait for the form to be visible and interactive
    await page.waitForSelector('input[placeholder="Enter item name"]', { state: 'visible' });

    // Fill in the name (description is optional, skip it)
    await page.fill('input[placeholder="Enter item name"]', 'E2E Test Item');

    // Submit
    await page.click('button[type="submit"]');

    // Wait for the item to appear in the list
    await page.waitForSelector('text=E2E Test Item', { timeout: 10_000 });

    // Verify item appears in list
    await expect(page.locator('text=E2E Test Item').first()).toBeVisible();
  });

  test('should show validation error for empty name', async ({ page }) => {
    await page.goto('/items');
    await page.waitForLoadState('domcontentloaded');

    // Wait for the form to be visible
    await page.waitForSelector('input[placeholder="Enter item name"]', { state: 'visible' });

    // Clear the field: click, select all, delete
    const nameInput = page.locator('input[placeholder="Enter item name"]');
    await nameInput.click();
    await nameInput.press('Control+a');
    await nameInput.press('Backspace');

    // Submit the form
    await page.click('button[type="submit"]');

    // Wait for validation error to appear
    await page.waitForSelector('text=Name is required', { timeout: 5_000 });
    await expect(page.locator('text=Name is required').first()).toBeVisible();
  });
});

test.describe('Search Items', () => {
  test('should filter items by search', async ({ page, request }) => {
    // Create a unique test item via API
    const uniqueName = `SearchTest_${Date.now()}`;
    await request.post(`${API_BASE}/items`, {
      data: {
        name: uniqueName,
        description: 'For search testing',
      },
    });

    await page.goto('/items');
    await page.waitForLoadState('domcontentloaded');

    // Type in search
    await page.fill('input[placeholder*="Search"]', uniqueName);

    // Wait for filter
    await page.waitForTimeout(500);

    // Should show filtered results (use first() to avoid strict mode violation)
    await expect(page.locator('text=' + uniqueName).first()).toBeVisible();
  });
});

test.describe('Delete Item', () => {
  test('should delete an item', async ({ page, request }) => {
    // Create a unique test item via API
    const uniqueName = `DeleteTest_${Date.now()}`;
    const createResponse = await request.post(`${API_BASE}/items`, {
      data: {
        name: uniqueName,
        description: 'To be deleted',
      },
    });
    (await createResponse.json()) as { id: string };

    await page.goto('/items');
    await page.waitForLoadState('domcontentloaded');

    // Wait for the item to appear in the list
    await page.waitForSelector('text=' + uniqueName, { timeout: 10_000 });

    // Click delete button in the same table row as the item name
    await page.locator(`tr:has-text("${uniqueName}")`).locator('button:has-text("Delete")').click();

    // Wait for modal to appear
    await page.waitForSelector('[role="dialog"]', { timeout: 5000 });

    // Confirm deletion by clicking the confirm button inside the modal
    // The modal has two buttons: cancel and confirm, confirm is the last one
    await page.locator('[role="dialog"] button:has-text("Delete")').last().click();

    // Wait for deletion to complete and page to refresh
    await page.waitForTimeout(1000);

    // Item should be removed from list
    await expect(page.locator('text=' + uniqueName).first()).not.toBeVisible({ timeout: 5000 });
  });
});
