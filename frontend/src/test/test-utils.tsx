/**
 * Custom test utilities with pre-wired providers.
 *
 * @remarks
 * Provides a `renderWithProviders` function that wraps test components with
 * all required providers (React Query, Toast, Feedback). Use this instead
 * of the default `render` from `@testing-library/react` to ensure consistent
 * test environment setup.
 *
 * @packageDocumentation
 */

import { render, type RenderOptions } from '@testing-library/react';
import React, { type ReactElement } from 'react';

import { GlobalProviders } from '@/app/(ui)/global-providers';

/**
 * Render a component with all required providers pre-wired.
 *
 * @remarks
 * Wraps the component with `GlobalProviders` (React Query + Toast + Feedback)
 * to ensure consistent test environment setup.
 *
 * @param ui - The React element to render.
 * @param options - Optional render options (passed through to `render`).
 * @returns The render result from `@testing-library/react`.
 *
 * @public
 */
export function renderWithProviders(
  ui: ReactElement,
  options?: Omit<RenderOptions, 'wrapper'>,
): ReturnType<typeof render> {
  /**
   * Internal wrapper component that provides all test providers.
   *
   * @param props - Child components to render.
   * @returns Provider-wrapped children.
   *
   * @internal
   */
  function Wrapper({ children }: { children: React.ReactNode }) {
    return <GlobalProviders>{children}</GlobalProviders>;
  }

  return render(ui, { wrapper: Wrapper, ...options });
}

// Re-export everything from testing-library
export * from '@testing-library/react';
