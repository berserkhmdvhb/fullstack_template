'use client';

/**
 * Global header with navigation.
 *
 * @remarks
 * Persistent top navigation bar with logo, links, and theme toggle.
 * Highlights the current page with an active indicator.
 *
 * @packageDocumentation
 */

import { Home, List, BookOpen, Moon, Sun } from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import React, { type JSX } from 'react';

import { API_BASE } from '@/lib/config';
import { useTheme } from './theme-provider';

// FastAPI serves its Swagger UI at the API *origin* root (`/docs`), NOT under
// the `/api/v1` prefix the browser uses for data calls. Derive that origin from
// the same `NEXT_PUBLIC_API_URL` the data client uses so the "API Docs" link
// resolves correctly in every environment (local ingress host, port-forward,
// or production `api.your-domain.example`). A bare relative `/docs` would resolve
// against the frontend origin, hit the Next.js router, and 404.
//
// API_BASE is absolute in every real deployment; the only relative case is the
// `/api/v1` same-origin fallback, for which `${API_ORIGIN}/docs` becomes `/docs`.
const API_ORIGIN = API_BASE.startsWith('http') ? new URL(API_BASE).origin : '';

const NAV_LINKS = [
  { href: '/', label: 'Home', icon: Home, prefetch: 'auto' as const },
  { href: '/items', label: 'Items', icon: List, prefetch: 'auto' as const },
  { href: `${API_ORIGIN}/docs`, label: 'API Docs', icon: BookOpen, prefetch: false as const },
];

/**
 * GlobalHeader component.
 *
 * @returns The header/navigation bar.
 *
 * @public
 */
export function GlobalHeader(): JSX.Element {
  const { theme, toggleTheme } = useTheme();
  const pathname = usePathname();

  return (
    <header className="border-b border-borderStrong bg-surfaceStrong/90 backdrop-blur-md">
      {/* Phone: the name and the theme toggle share the first row; the links take the second. */}
      <nav
        aria-label="Main"
        className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-1 gap-y-1 px-4 py-2 sm:px-6 sm:py-3"
      >
        {/* Logo */}
        <Link
          href="/"
          className="mr-auto flex min-h-9 items-center gap-2.5 whitespace-nowrap rounded-md"
        >
          <span className="text-base font-semibold text-textPrimary tracking-tight">
            Your Service Name
          </span>
        </Link>

        {/* Nav links */}
        <div className="order-last flex w-full items-center gap-1 sm:order-none sm:w-auto">
          {NAV_LINKS.map((link) => {
            const isActive = link.href === '/' ? pathname === '/' : pathname.startsWith(link.href);
            const Icon = link.icon;
            return (
              <Link
                key={link.href}
                href={link.href}
                // /docs is the backend's page: prefetching it would make the frontend proxy call the backend.
                prefetch={link.prefetch}
                className={`group flex min-h-9 items-center gap-2 whitespace-nowrap rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                  isActive
                    ? 'bg-surfaceWeak text-textPrimary'
                    : 'text-textSecondary hover:bg-surfaceWeak hover:text-textPrimary'
                }`}
                aria-current={isActive ? 'page' : undefined}
              >
                <Icon className="h-4 w-4" aria-hidden="true" />
                <span>{link.label}</span>
                {isActive && (
                  <span className="absolute -bottom-px left-0 right-0 h-0.5 rounded-full bg-accentBlue" />
                )}
              </Link>
            );
          })}
        </div>

        {/* Separator (desktop only) */}
        <div className="mx-2 hidden h-5 w-px bg-borderStrong sm:block" aria-hidden="true" />

        {/* Theme toggle */}
        <button
          type="button"
          onClick={toggleTheme}
          className="flex h-9 w-9 items-center justify-center rounded-md text-textSecondary hover:bg-surfaceWeak hover:text-textPrimary transition-colors"
          aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
          title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
        >
          {theme === 'dark' ? (
            <Sun className="h-4 w-4" aria-hidden="true" />
          ) : (
            <Moon className="h-4 w-4" aria-hidden="true" />
          )}
        </button>
      </nav>
    </header>
  );
}
