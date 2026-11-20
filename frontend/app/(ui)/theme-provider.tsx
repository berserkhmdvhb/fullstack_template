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

import type { ReactNode } from 'react';
import React, { createContext, useContext, useEffect, useState } from 'react';

type Theme = 'light' | 'dark';

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
 * - Reads theme from localStorage on mount
 * - Applies theme class to <html> element
 * - Provides toggle function to children
 *
 * @param props - Provider children.
 * @returns Theme context provider.
 *
 * @public
 */
export function ThemeProvider({ children }: { children: ReactNode }): JSX.Element {
  // Initialize from localStorage, falling back to system preference.
  const [theme, setTheme] = useState<Theme>(() => {
    if (typeof window === 'undefined') return 'dark';
    const stored = localStorage.getItem('theme') as Theme | null;
    if (stored === 'light' || stored === 'dark') return stored;
    // Fall back to system preference
    if (window.matchMedia('(prefers-color-scheme: light)').matches) return 'light';
    return 'dark';
  });

  useEffect(() => {
    // Apply theme to <html> element and persist to localStorage.
    document.documentElement.classList.remove('light', 'dark');
    document.documentElement.classList.add(theme);
    localStorage.setItem('theme', theme);
  }, [theme]);

  const toggleTheme = () => {
    setTheme((prev) => (prev === 'dark' ? 'light' : 'dark'));
  };

  return <ThemeContext.Provider value={{ theme, toggleTheme }}>{children}</ThemeContext.Provider>;
}
