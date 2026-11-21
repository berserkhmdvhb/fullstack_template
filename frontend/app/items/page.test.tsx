/**
 * Tests for the items page: the list and the form working together.
 *
 * @packageDocumentation
 */

import userEvent from '@testing-library/user-event';
import React from 'react';
import { describe, expect, it } from 'vitest';

import { renderWithProviders, screen, waitFor } from '@/src/test/test-utils';

import ItemsPage from './page';

describe('ItemsPage', () => {
  it('shows the heading, the item list and the create form', async () => {
    renderWithProviders(<ItemsPage />);

    expect(screen.getByRole('heading', { name: 'Items', level: 1 })).toBeInTheDocument();
    expect(await screen.findByText('Alpha Item')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Create Item' })).toBeInTheDocument();
  });

  it('switches the form to edit mode for the clicked item, and back on cancel', async () => {
    const user = userEvent.setup();
    renderWithProviders(<ItemsPage />);

    await user.click(await screen.findByRole('button', { name: 'Edit Alpha Item' }));

    expect(screen.getByRole('heading', { name: 'Edit Item' })).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByLabelText(/^Name/)).toHaveValue('Alpha Item');
    });

    await user.click(screen.getByRole('button', { name: 'Cancel editing' }));

    expect(screen.getByRole('heading', { name: 'Create Item' })).toBeInTheDocument();
  });
});
