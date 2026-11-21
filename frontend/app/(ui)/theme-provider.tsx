/**
 * Theme provider for light/dark mode toggle.
 *
 * @remarks
 * - Persists theme preference in localStorage
 * - Defaults to dark mode
 * - Provides theme context to children
 *
 * @packageDocumentation
 */

'use client';

import type { ReactNode, JSX } from 'react';
import React, { createContext, useContext, useSyncExternalStore } from 'react';

type Theme = 'light' | 'dark';

/** The theme the page is showing: the class the init script (or a toggle) put on `<html>`. */
function getTheme(): Theme {
  return document.documentElement.classList.contains('light') ? 'light' : 'dark';
}

/** Re-render when the `html` class changes. */
function subscribe(onChange: () => void): () => void {
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
  return () => {
    observer.disconnect();
  };
}

/** The server always renders dark; the browser then reads the real theme (no hydration mismatch). */
const getServerTheme = (): Theme => 'dark';

type ThemeContextType = {
  theme: Theme;
  toggleTheme: () => void;
};

const ThemeContext = createContext<ThemeContextType>({
  theme: 'dark',
  toggleTheme: () => {},
});

/**
 * Hook to access theme context.
 *
 * @returns Theme context with current theme and toggle function.
 *
 * @public
 */
export function useTheme(): ThemeContextType {
  return useContext(ThemeContext);
}

/**
 * ThemeProvider component.
 *
 * @remarks
 * - Shows the theme the init script put on <html> (hydration-safe, see below)
 * - Toggling swaps the <html> class and saves the choice to localStorage
 * - Provides toggle function to children
 *
 * @param props - Provider children.
 * @returns Theme context provider.
 *
 * @public
 */
export function ThemeProvider({ children }: { children: ReactNode }): JSX.Element {
  // useSyncExternalStore is React's tool for a client-only value: it renders the server snapshot while
  // hydrating, then re-renders with the real one (https://react.dev/reference/react/useSyncExternalStore).
  const theme = useSyncExternalStore(subscribe, getTheme, getServerTheme);

  const toggleTheme = () => {
    const next: Theme = getTheme() === 'dark' ? 'light' : 'dark';
    document.documentElement.classList.remove('light', 'dark');
    document.documentElement.classList.add(next);
    try {
      localStorage.setItem('theme', next);
    } catch {
      // Storage is blocked (private mode, a policy): the theme still switches for this session.
    }
  };

  return <ThemeContext.Provider value={{ theme, toggleTheme }}>{children}</ThemeContext.Provider>;
}
