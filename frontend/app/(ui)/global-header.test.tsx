/**
 * Tests for global header component.
 *
 * @packageDocumentation
 */

import React from 'react';

import { describe, expect, it } from 'vitest';

import { renderWithProviders, screen, within } from '@/src/test/test-utils';

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

  it('should expose a labelled navigation landmark with the links in order', () => {
    renderWithProviders(<GlobalHeader />);

    const nav = screen.getByRole('navigation', { name: 'Main' });
    expect(
      within(nav)
        .getAllByRole('link')
        .map((l) => l.textContent),
    ).toEqual(['Your Service Name', 'Home', 'Items', 'API Docs']);
  });

  it('should mark only the current page and keep the icons out of the accessibility tree', () => {
    renderWithProviders(<GlobalHeader />);

    const current = screen.getAllByRole('link').filter((l) => l.hasAttribute('aria-current'));
    expect(current.map((l) => l.textContent)).toEqual(['Home']);
    expect(document.querySelectorAll('nav svg:not([aria-hidden="true"])')).toHaveLength(0);
  });
});
