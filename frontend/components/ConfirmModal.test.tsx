/**
 * Tests for confirmation modal component.
 *
 * @packageDocumentation
 */

import React from 'react';

import { describe, expect, it, vi } from 'vitest';

import { renderWithProviders, screen } from '@/src/test/test-utils';

import { ConfirmModal } from './ConfirmModal';

describe('ConfirmModal', () => {
  it('should not render when isOpen is false', () => {
    renderWithProviders(
      <ConfirmModal
        isOpen={false}
        title="Test"
        message="Test message"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('should render when isOpen is true', () => {
    renderWithProviders(
      <ConfirmModal
        isOpen={true}
        title="Confirm Action"
        message="Are you sure?"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('should render title', () => {
    renderWithProviders(
      <ConfirmModal
        isOpen={true}
        title="Confirm Action"
        message="Are you sure?"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByText('Confirm Action')).toBeInTheDocument();
  });

  it('should render message', () => {
    renderWithProviders(
      <ConfirmModal
        isOpen={true}
        title="Confirm Action"
        message="Are you sure?"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByText('Are you sure?')).toBeInTheDocument();
  });

  it('should render confirm button', () => {
    renderWithProviders(
      <ConfirmModal
        isOpen={true}
        title="Confirm Action"
        message="Are you sure?"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByText('Confirm')).toBeInTheDocument();
  });

  it('should render cancel button', () => {
    renderWithProviders(
      <ConfirmModal
        isOpen={true}
        title="Confirm Action"
        message="Are you sure?"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByText('Cancel')).toBeInTheDocument();
  });

  it('should call onConfirm when confirm button is clicked', async () => {
    const { userEvent } = await import('@testing-library/user-event');
    const mockOnConfirm = vi.fn();

    renderWithProviders(
      <ConfirmModal
        isOpen={true}
        title="Confirm Action"
        message="Are you sure?"
        onConfirm={mockOnConfirm}
        onCancel={vi.fn()}
      />,
    );

    const user = userEvent.setup();
    await user.click(screen.getByText('Confirm'));

    expect(mockOnConfirm).toHaveBeenCalledTimes(1);
  });

  it('should call onCancel when cancel button is clicked', async () => {
    const { userEvent } = await import('@testing-library/user-event');
    const mockOnCancel = vi.fn();

    renderWithProviders(
      <ConfirmModal
        isOpen={true}
        title="Confirm Action"
        message="Are you sure?"
        onConfirm={vi.fn()}
        onCancel={mockOnCancel}
      />,
    );

    const user = userEvent.setup();
    await user.click(screen.getByText('Cancel'));

    expect(mockOnCancel).toHaveBeenCalledTimes(1);
  });

  it('should use custom button labels', () => {
    renderWithProviders(
      <ConfirmModal
        isOpen={true}
        title="Delete Item"
        message="This cannot be undone"
        confirmLabel="Delete"
        cancelLabel="Keep"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByText('Delete')).toBeInTheDocument();
    expect(screen.getByText('Keep')).toBeInTheDocument();
  });
});
