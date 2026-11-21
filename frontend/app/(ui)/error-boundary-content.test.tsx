/**
 * Tests for error boundary content component.
 *
 * @packageDocumentation
 */

import React from 'react';

import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';

import { renderWithProviders, screen } from '@/src/test/test-utils';

import { ErrorBoundaryContent } from './error-boundary-content';

// The boundary logs the caught error with console.error; keep that out of the test output and check it.
let consoleError: MockInstance<typeof console.error>;
beforeEach(() => {
  consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
});
afterEach(() => {
  consoleError.mockRestore();
});

describe('ErrorBoundaryContent', () => {
  it('should render heading', () => {
    const mockError = new Error('Test error');
    const mockReset = vi.fn();

    renderWithProviders(<ErrorBoundaryContent error={mockError} reset={mockReset} />);

    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
    expect(consoleError).toHaveBeenCalledWith('Error boundary captured error:', mockError);
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
