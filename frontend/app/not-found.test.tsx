/**
 * Tests for the custom 404 page.
 *
 * @packageDocumentation
 */

import React from 'react';

import { describe, expect, it } from 'vitest';

import { render, screen } from '@/src/test/test-utils';

import NotFound from './not-found';

describe('NotFound', () => {
  it('tells the visitor the page does not exist', () => {
    render(<NotFound />);

    expect(screen.getByRole('heading', { name: 'Page Not Found' })).toBeInTheDocument();
    expect(screen.getByText(/doesn't exist or has been moved/)).toBeInTheDocument();
  });

  it('offers a way back to the home page', () => {
    render(<NotFound />);

    expect(screen.getByRole('link', { name: 'Return Home' })).toHaveAttribute('href', '/');
  });
});
