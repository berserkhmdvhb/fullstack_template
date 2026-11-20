/**
 * Tests for error boundary content component.
 *
 * @packageDocumentation
 */

import React from 'react';

import { describe, expect, it, vi } from 'vitest';

import { renderWithProviders, screen } from '@/src/test/test-utils';

import { ErrorBoundaryContent } from './error-boundary-content';

describe('ErrorBoundaryContent', () => {
  it('should render heading', () => {
    const mockError = new Error('Test error');
    const mockReset = vi.fn();

    renderWithProviders(<ErrorBoundaryContent error={mockError} reset={mockReset} />);

    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
  });

  it('should render description', () => {
    const mockError = new Error('Test error');
    const mockReset = vi.fn();

    renderWithProviders(<ErrorBoundaryContent error={mockError} reset={mockReset} />);

    expect(screen.getByText(/An unexpected error occurred/)).toBeInTheDocument();
  });

  it('should render try again button', () => {
    const mockError = new Error('Test error');
    const mockReset = vi.fn();

    renderWithProviders(<ErrorBoundaryContent error={mockError} reset={mockReset} />);

    const button = screen.getByText('Try Again');
    expect(button).toBeInTheDocument();
  });

  it('should call reset when try again is clicked', async () => {
    const { userEvent } = await import('@testing-library/user-event');
    const mockError = new Error('Test error');
    const mockReset = vi.fn();

    renderWithProviders(<ErrorBoundaryContent error={mockError} reset={mockReset} />);

    const user = userEvent.setup();
    await user.click(screen.getByText('Try Again'));

    expect(mockReset).toHaveBeenCalledTimes(1);
  });

  it('should render return home link', () => {
    const mockError = new Error('Test error');
    const mockReset = vi.fn();

    renderWithProviders(<ErrorBoundaryContent error={mockError} reset={mockReset} />);

    expect(screen.getByText('Return Home')).toBeInTheDocument();
  });
});
