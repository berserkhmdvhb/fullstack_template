/**
 * Global error boundary screen.
 *
 * @remarks
 * Captures unexpected client-side render errors and displays a themed fallback.
 * Wraps the entire app and requires its own `<html>/<body>`.
 *
 * @packageDocumentation
 */

'use client';

import { ErrorBoundaryContent } from '@/app/(ui)/error-boundary-content';

type Props = {
  error: unknown;
  reset: () => void;
};

/**
 * GlobalError (client boundary).
 *
 * @param props - Contains the caught error object and reset function.
 * @returns A themed fallback error screen.
 *
 * @public
 */
export default function GlobalError({ error, reset }: Props): JSX.Element {
  return (
    <html lang="en">
      <body className="flex min-h-screen items-center justify-center px-4">
        <ErrorBoundaryContent error={error} reset={reset} />
      </body>
    </html>
  );
}
