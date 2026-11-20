/**
 * Global loading fallback (App Router).
 *
 * @remarks
 * Used automatically by Next.js during suspense boundaries at the root level.
 *
 * @packageDocumentation
 */

import React from 'react';

/**
 * Loading screen shown while initial page segments are being fetched.
 *
 * @returns A themed loading panel with a spinner.
 *
 * @public
 */
export default function Loading(): JSX.Element {
  return (
    <div className="panel py-12 text-center">
      <div className="mx-auto h-6 w-6 animate-spin rounded-full border-2 border-accentBlue border-t-transparent" />
      <p className="mt-4 text-sm text-textSecondary">Loading...</p>
    </div>
  );
}
