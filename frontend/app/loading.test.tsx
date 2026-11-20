/**
 * Tests for Loading UI component.
 *
 * @packageDocumentation
 */

import React from 'react';

import { describe, expect, it } from 'vitest';

import { renderWithProviders, screen } from '@/src/test/test-utils';

import Loading from './loading';

describe('Loading', () => {
  it('should render loading text', () => {
    renderWithProviders(<Loading />);

    expect(screen.getByText('Loading...')).toBeInTheDocument();
  });
});
