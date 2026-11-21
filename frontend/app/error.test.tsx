/**
 * Tests for route-level error boundary.
 *
 * @packageDocumentation
 */

import React from 'react';

import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';

import { renderWithProviders, screen } from '@/src/test/test-utils';

import ErrorBoundary from './error';

// The boundary logs the caught error with console.error; keep that out of the test output and check it.
let consoleError: MockInstance<typeof console.error>;
beforeEach(() => {
  consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
});
afterEach(() => {
  consoleError.mockRestore();
});

describe('ErrorBoundary', () => {
  it('should render error boundary content', () => {
    const mockError = new Error('Test error');
    const mockReset = vi.fn();

    renderWithProviders(<ErrorBoundary error={mockError} reset={mockReset} />);

    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
    expect(consoleError).toHaveBeenCalledWith('Error boundary captured error:', mockError);
  });

  it('should render reset button', () => {
    const mockError = new Error('Test error');
    const mockReset = vi.fn();

    renderWithProviders(<ErrorBoundary error={mockError} reset={mockReset} />);

    expect(screen.getByText('Try Again')).toBeInTheDocument();
  });

  it('should render return home link', () => {
    const mockError = new Error('Test error');
    const mockReset = vi.fn();

    renderWithProviders(<ErrorBoundary error={mockError} reset={mockReset} />);

    expect(screen.getByText('Return Home')).toBeInTheDocument();
  });
});
