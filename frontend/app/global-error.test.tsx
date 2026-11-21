/**
 * Tests for the global error boundary screen.
 *
 * @remarks
 * It renders its own `<html>` and `<body>`, so it is rendered to a string rather than into a test container.
 *
 * @packageDocumentation
 */

import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';

import GlobalError from './global-error';

describe('GlobalError', () => {
  it('renders a complete document with the error fallback', () => {
    const view = renderToStaticMarkup(<GlobalError error={new Error('boom')} reset={vi.fn()} />);

    expect(view).toMatch(/^<html lang="en">/);
    expect(view).toContain('<body');
    expect(view).toContain('Something went wrong');
    expect(view).toContain('Try Again');
    expect(view).toContain('href="/"');
  });

  it('does not leak the error message to the page', () => {
    const view = renderToStaticMarkup(
      <GlobalError error={new Error('secret detail')} reset={vi.fn()} />,
    );

    expect(view).not.toContain('secret detail');
  });
});
