/**
 * SAMPLE/REFERENCE: Tests for ItemForm component.
 *
 * @remarks
 * Tests form rendering, validation, submission, and error states.
 *
 * @packageDocumentation
 */

import { HttpResponse, http } from 'msw/http';
import React from 'react';
import { describe, expect, it } from 'vitest';

import { renderWithProviders, screen, waitFor } from '@/src/test/test-utils';
import { API_PATH } from '@/static-paths';

import { ItemForm } from './ItemForm';

// ---------------------------------------------------------------------------
// Tests: Rendering
// ---------------------------------------------------------------------------

describe('ItemForm', () => {
  it('renders the form with heading', () => {
    renderWithProviders(<ItemForm />);
    expect(screen.getByRole('heading', { name: 'Create Item' })).toBeInTheDocument();
  });

  it('renders name input', () => {
    renderWithProviders(<ItemForm />);
    expect(screen.getByLabelText(/Name/)).toBeInTheDocument();
  });

  it('renders description textarea', () => {
    renderWithProviders(<ItemForm />);
    expect(screen.getByLabelText(/Description/)).toBeInTheDocument();
  });

  it('renders is_active checkbox', () => {
    renderWithProviders(<ItemForm />);
    expect(screen.getByLabelText('Active')).toBeInTheDocument();
  });

  it('renders submit button', () => {
    renderWithProviders(<ItemForm />);
    expect(screen.getByRole('button', { name: /Create/ })).toBeInTheDocument();
  });

  // ---------------------------------------------------------------------------
  // Tests: Validation
  // ---------------------------------------------------------------------------

  it('shows error when name is empty on submit', async () => {
    const { userEvent } = await import('@testing-library/user-event');
    const user = userEvent.setup();

    renderWithProviders(<ItemForm />);

    // Submit without filling name
    await user.click(screen.getByRole('button', { name: /Create/ }));

    await waitFor(() => {
      expect(screen.getByText('Name is required')).toBeInTheDocument();
    });
  });

  it('shows error when name exceeds 255 characters', async () => {
    const { userEvent } = await import('@testing-library/user-event');
    const user = userEvent.setup();

    renderWithProviders(<ItemForm />);

    const nameInput = screen.getByLabelText(/Name/);
    await user.type(nameInput, 'x'.repeat(256));
    await user.click(screen.getByRole('button', { name: /Create/ }));

    await waitFor(() => {
      expect(screen.getByText(/255 characters or less/)).toBeInTheDocument();
    });
  });

  it('submits successfully with valid data', async () => {
    const { userEvent } = await import('@testing-library/user-event');
    const user = userEvent.setup();

    renderWithProviders(<ItemForm />);

    const nameInput = screen.getByLabelText(/Name/);
    const descInput = screen.getByLabelText(/Description/);

    await user.type(nameInput, 'Test Item');
    await user.type(descInput, 'A test description');
    await user.click(screen.getByRole('button', { name: /Create/ }));

    // After successful submission, form should reset
    await waitFor(() => {
      expect(nameInput).toHaveValue('');
    });
  });

  // ---------------------------------------------------------------------------
  // Tests: Default values
  // ---------------------------------------------------------------------------

  it('has empty name by default', () => {
    renderWithProviders(<ItemForm />);
    expect(screen.getByLabelText(/Name/)).toHaveValue('');
  });

  it('has empty description by default', () => {
    renderWithProviders(<ItemForm />);
    expect(screen.getByLabelText(/Description/)).toHaveValue('');
  });

  // ---------------------------------------------------------------------------
  // Tests: Error state
  // ---------------------------------------------------------------------------

  it('shows error message when creation fails', async () => {
    const { userEvent } = await import('@testing-library/user-event');
    const user = userEvent.setup();

    // Override MSW to return error
    const { server } = await import('@/src/test/mocks/server');
    server.use(
      http.post(`${API_PATH}/items`, () => {
        return HttpResponse.json({ detail: 'Creation failed' }, { status: 500 });
      }),
    );

    renderWithProviders(<ItemForm />);

    const nameInput = screen.getByLabelText(/Name/);
    await user.type(nameInput, 'Fail Item');
    await user.click(screen.getByRole('button', { name: /Create/ }));

    await waitFor(() => {
      expect(screen.getByText(/Failed to create item:/)).toBeInTheDocument();
    });
  });

  // ---------------------------------------------------------------------------
  // Tests: Disabled state
  // ---------------------------------------------------------------------------

  it('disables submit button while submitting', async () => {
    const { userEvent } = await import('@testing-library/user-event');
    const user = userEvent.setup();

    // Use a delayed handler to catch the submitting state
    const { server } = await import('@/src/test/mocks/server');
    server.use(
      http.post(`${API_PATH}/items`, async () => {
        await new Promise((resolve) => setTimeout(resolve, 100));
        return HttpResponse.json(
          {
            id: 'test-id',
            name: 'Test',
            description: '',
            is_active: true,
            created_at: '2025-01-01T00:00:00Z',
            updated_at: '2025-01-01T00:00:00Z',
          },
          { status: 201 },
        );
      }),
    );

    renderWithProviders(<ItemForm />);

    const nameInput = screen.getByLabelText(/Name/);
    await user.type(nameInput, 'Slow Item');

    const submitButton = screen.getByRole('button', { name: /Create/ });
    await user.click(submitButton);

    // Button should show "Creating..." text during submission
    await waitFor(() => {
      expect(screen.getByText('Creating...')).toBeInTheDocument();
    });

    // Let the delayed request settle so the window teardown has nothing in flight to abort
    await waitFor(() => {
      expect(screen.queryByText('Creating...')).not.toBeInTheDocument();
    });
  });
});
