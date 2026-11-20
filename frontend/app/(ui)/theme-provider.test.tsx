/**
 * Tests for theme provider.
 *
 * @packageDocumentation
 */

import React from 'react';

import { beforeAll, describe, expect, it, vi } from 'vitest';

import { renderWithProviders, screen } from '@/src/test/test-utils';

import { ThemeProvider, useTheme } from './theme-provider';

beforeAll(() => {
  // Mock matchMedia so prefers-color-scheme: light returns false (dark default)
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query.includes('light') ? false : true,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
    media: query,
  }));
});

describe('useTheme', () => {
  it('should return theme and toggleTheme', () => {
    let themeValue: { theme: string; toggleTheme: () => void } | undefined;

    /** Test component that captures theme context values. */
    function TestComponent() {
      const { theme, toggleTheme } = useTheme();
      themeValue = { theme, toggleTheme };
      return null;
    }

    renderWithProviders(
      <ThemeProvider>
        <TestComponent />
      </ThemeProvider>,
    );

    expect(themeValue).toBeDefined();
    expect(themeValue!.theme).toBe('dark'); // default
    expect(typeof themeValue!.toggleTheme).toBe('function');
  });
});

describe('ThemeProvider', () => {
  it('should default to dark theme', () => {
    /** Test component that displays the current theme. */
    function TestComponent() {
      const { theme } = useTheme();
      return <div>{theme}</div>;
    }

    renderWithProviders(
      <ThemeProvider>
        <TestComponent />
      </ThemeProvider>,
    );

    expect(screen.getByText('dark')).toBeInTheDocument();
  });

  it('should toggle theme', async () => {
    const { userEvent } = await import('@testing-library/user-event');

    /** Test component that displays theme and a toggle button. */
    function TestComponent() {
      const { theme, toggleTheme } = useTheme();
      return (
        <div>
          <span>{theme}</span>
          <button onClick={toggleTheme}>Toggle</button>
        </div>
      );
    }

    renderWithProviders(
      <ThemeProvider>
        <TestComponent />
      </ThemeProvider>,
    );

    const user = userEvent.setup();

    // Initial theme is dark
    expect(screen.getByText('dark')).toBeInTheDocument();

    // Toggle to light
    await user.click(screen.getByText('Toggle'));
    expect(screen.getByText('light')).toBeInTheDocument();

    // Toggle back to dark
    await user.click(screen.getByText('Toggle'));
    expect(screen.getByText('dark')).toBeInTheDocument();
  });
});
