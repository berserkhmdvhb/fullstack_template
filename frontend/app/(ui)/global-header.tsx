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
import React from 'react';

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
  { href: '/', label: 'Home', icon: Home },
  { href: '/items', label: 'Items', icon: List },
  { href: `${API_ORIGIN}/docs`, label: 'API Docs', icon: BookOpen },
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
      <nav className="mx-auto flex max-w-6xl items-center justify-between px-6 py-3">
        {/* Logo */}
        <Link href="/" className="flex items-center gap-2.5">
          <span className="text-base font-semibold text-textPrimary tracking-tight">
            Your Service Name
          </span>
        </Link>

        {/* Nav + Theme */}
        <div className="flex items-center gap-1">
          {/* Nav links */}
          <div className="flex items-center gap-1">
            {NAV_LINKS.map((link) => {
              const isActive =
                link.href === '/' ? pathname === '/' : pathname.startsWith(link.href);
              const Icon = link.icon;
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  className={`group flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                    isActive
                      ? 'bg-surfaceWeak text-textPrimary'
                      : 'text-textSecondary hover:bg-surfaceWeak hover:text-textPrimary'
                  }`}
                  aria-current={isActive ? 'page' : undefined}
                >
                  <Icon className="h-4 w-4" />
                  <span>{link.label}</span>
                  {isActive && (
                    <span className="absolute -bottom-px left-0 right-0 h-0.5 rounded-full bg-accentBlue" />
                  )}
                </Link>
              );
            })}
          </div>

          {/* Separator */}
          <div className="mx-2 h-5 w-px bg-borderStrong" />

          {/* Theme toggle */}
          <button
            type="button"
            onClick={toggleTheme}
            className="rounded-md p-2 text-textSecondary hover:bg-surfaceWeak hover:text-textPrimary transition-colors"
            aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
            title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
          >
            {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </button>
        </div>
      </nav>
    </header>
  );
}
