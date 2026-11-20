/**
 * Tests for global header component.
 *
 * @packageDocumentation
 */

import React from 'react';

import { describe, expect, it } from 'vitest';

import { renderWithProviders, screen } from '@/src/test/test-utils';

import { GlobalHeader } from './global-header';

describe('GlobalHeader', () => {
  it('should render service title', () => {
    renderWithProviders(<GlobalHeader />);

    expect(screen.getByText('Your Service Name')).toBeInTheDocument();
  });

  it('should render Home link', () => {
    renderWithProviders(<GlobalHeader />);

    expect(screen.getByText('Home')).toBeInTheDocument();
  });

  it('should render Items link', () => {
    renderWithProviders(<GlobalHeader />);

    expect(screen.getByText('Items')).toBeInTheDocument();
  });

  it('should render API Docs link', () => {
    renderWithProviders(<GlobalHeader />);

    expect(screen.getByText('API Docs')).toBeInTheDocument();
  });

  it('should render theme toggle button', () => {
    renderWithProviders(<GlobalHeader />);

    // Theme toggle button should have an aria-label
    const toggleButton = screen.getByLabelText(/Switch to/);
    expect(toggleButton).toBeInTheDocument();
  });
});
