/**
 * Custom 404 (Not Found) page.
 *
 * @remarks
 * Rendered by Next.js when a route does not match any defined path.
 * Provides a themed fallback instead of the default Next.js 404 page.
 *
 * @packageDocumentation
 */

import Link from 'next/link';

/**
 * Custom 404 page (server component).
 *
 * @returns A themed "page not found" screen.
 *
 * @public
 */
export default function NotFound(): JSX.Element {
  return (
    <div className="card max-w-md space-y-4 text-center mx-auto my-12">
      <h2 className="text-lg font-semibold text-white">Page Not Found</h2>
      <p className="text-textTertiary">
        The page you&apos;re looking for doesn&apos;t exist or has been moved.
      </p>
      <Link href="/" className="btn-primary inline-block">
        Return Home
      </Link>
    </div>
  );
}
