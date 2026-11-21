/**
 * Root application layout (App Router).
 *
 * @remarks
 * Provides the global UI shell:
 * - Global CSS & theme (with light/dark toggle)
 * - Accessibility skip-link
 * - Global client providers (React Query, Toast system)
 * - Persistent, responsive top navigation
 *
 * @packageDocumentation
 */

import { headers } from 'next/headers';
import type { ReactNode, JSX } from 'react';
import '@/styles/globals.css';

import { GlobalHeader } from './(ui)/global-header';
import { GlobalProviders } from './(ui)/global-providers';
import { ThemeProvider } from './(ui)/theme-provider';

export const metadata = {
  title: 'Your Service Name',
  description: 'Frontend UI for Your Service Name',
};

/**
 * RootLayout (Server Component).
 *
 * @param props - Layout children.
 * @returns Root application UI shell.
 *
 * @public
 */
export default async function RootLayout({
  children,
}: {
  children: ReactNode;
}): Promise<JSX.Element> {
  // Per-request CSP nonce minted by proxy.ts and forwarded as `x-nonce`.
  // Next.js reads the same nonce out of the CSP header and applies it to its
  // inline bootstrap scripts, so mirroring it here on `<html>` lets the App
  // Router hydrate under a strict CSP with no `'unsafe-inline'`.
  const nonce = (await headers()).get('x-nonce') ?? undefined;
  return (
    <html lang="en" suppressHydrationWarning nonce={nonce}>
      <body>
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:top-4 focus:left-4 focus:z-50 focus:rounded focus:bg-primary focus:px-4 focus:py-2 focus:text-white"
        >
          Skip to content
        </a>
        <ThemeProvider>
          <GlobalHeader />
          <GlobalProviders>
            <main id="main" className="mx-auto max-w-6xl px-6 py-6">
              {children}
            </main>
          </GlobalProviders>
          <div id="toast-root" />
        </ThemeProvider>
      </body>
    </html>
  );
}
