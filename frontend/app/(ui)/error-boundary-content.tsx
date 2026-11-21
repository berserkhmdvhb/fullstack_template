/**
 * Shared error boundary content component.
 *
 * @remarks
 * Renders a themed fallback error card with retry and navigation options.
 * Used by both route-level (error.tsx) and global (global-error.tsx) boundaries.
 *
 * @packageDocumentation
 */

'use client';

import Link from 'next/link';
import React, { useEffect, type JSX } from 'react';

type Props = {
  error: unknown;
  reset: () => void;
};

/**
 * ErrorBoundaryContent renders the shared error UI.
 *
 * @param props - Contains the caught error object and reset function.
 * @returns A themed fallback error card.
 *
 * @public
 */
export function ErrorBoundaryContent({ error, reset }: Props): JSX.Element {
  useEffect(() => {
    console.error('Error boundary captured error:', error);
  }, [error]);

  return (
    <div className="card max-w-md space-y-4 text-center">
      <h2 className="text-lg font-semibold text-white">Something went wrong</h2>
      <p className="text-textTertiary">
        An unexpected error occurred. You can try again or return home.
      </p>
      <div className="flex gap-3 justify-center">
        <button onClick={reset} className="btn-primary">
          Try Again
        </button>
        <Link href="/" className="btn-neutral">
          Return Home
        </Link>
      </div>
    </div>
  );
}
