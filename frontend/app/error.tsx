/**
 * Route-level error boundary.
 *
 * @remarks
 * Catches unexpected client-side render errors within a route segment and
 * displays a themed fallback. Unlike `global-error.tsx` (which wraps the
 * entire app and requires its own `<html>/<body>`), this boundary operates
 * within the existing layout and allows partial page recovery.
 *
 * @packageDocumentation
 */

'use client';

import React, { type JSX } from 'react';
import { ErrorBoundaryContent } from '@/app/(ui)/error-boundary-content';

type Props = {
  error: unknown;
  reset: () => void;
};

/**
 * Route-level error boundary (client component).
 *
 * @param props - Contains the caught error object and reset function.
 * @returns A themed fallback error card within the existing layout.
 *
 * @public
 */
export default function Error({ error, reset }: Props): JSX.Element {
  return (
    <div className="mx-auto my-12">
      <ErrorBoundaryContent error={error} reset={reset} />
    </div>
  );
}
