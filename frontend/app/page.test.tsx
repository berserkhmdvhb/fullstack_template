/**
 * Integration test for the home page.
 *
 * @packageDocumentation
 */

import React from 'react';
import { describe, expect, it } from 'vitest';

import { renderWithProviders, screen, waitFor } from '@/src/test/test-utils';

import Home from './page';

describe('Home page', () => {
  it('should render dashboard title', async () => {
    renderWithProviders(<Home />);

    // Wait for the page to render
    await waitFor(() => {
      expect(screen.getByText(/Dashboard/)).toBeInTheDocument();
    });
  });

  it('should show getting started section', async () => {
    renderWithProviders(<Home />);

    await waitFor(() => {
      expect(screen.getByText(/Getting Started/)).toBeInTheDocument();
    });
  });

  it('should show system status section', async () => {
    renderWithProviders(<Home />);

    await waitFor(() => {
      expect(screen.getByText(/System Status/)).toBeInTheDocument();
    });
  });
});
