/**
 * Tests for route-level error boundary.
 *
 * @packageDocumentation
 */

import React from 'react';

import { describe, expect, it, vi } from 'vitest';

import { renderWithProviders, screen } from '@/src/test/test-utils';

import Error from './error';

describe('Error', () => {
  it('should render error boundary content', () => {
    const mockError = new Error('Test error');
    const mockReset = vi.fn();

    renderWithProviders(<Error error={mockError} reset={mockReset} />);

    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
  });

  it('should render reset button', () => {
    const mockError = new Error('Test error');
    const mockReset = vi.fn();

    renderWithProviders(<Error error={mockError} reset={mockReset} />);

    expect(screen.getByText('Try Again')).toBeInTheDocument();
  });

  it('should render return home link', () => {
    const mockError = new Error('Test error');
    const mockReset = vi.fn();

    renderWithProviders(<Error error={mockError} reset={mockReset} />);

    expect(screen.getByText('Return Home')).toBeInTheDocument();
  });
});
