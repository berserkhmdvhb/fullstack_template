/**
 * Home page module.
 *
 * @remarks
 * Dashboard landing page demonstrating backend connectivity and quick links.
 *
 * @packageDocumentation
 */

'use client';

import { ClipboardList, BookOpen, HeartPulse, Code2, Zap, Database } from 'lucide-react';
import Link from 'next/link';
import React from 'react';

import { useHealth } from '@/lib/hooks/useHealth';

/**
 * Dashboard page component.
 *
 * @remarks
 * Displays backend health status, API quick links, and getting started guidance.
 *
 * @returns The dashboard view.
 *
 * @public
 */
export default function Home(): JSX.Element {
  // Backend health via React Query (declarative; no hand-rolled fetch).
  const { data, isLoading, isError } = useHealth();
  const status: 'checking' | 'ok' | 'unreachable' = isError
    ? 'unreachable'
    : data?.status === 'ok'
      ? 'ok'
      : isLoading
        ? 'checking'
        : 'unreachable';

  return (
    <div className="space-y-6">
      {/* Welcome section */}
      <header className="card">
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-textPrimary">Dashboard</h1>
            <p className="mt-1 text-textPrimary">
              FastAPI backend + React/TypeScript frontend (Next.js).
            </p>
          </div>
          <Zap className="h-6 w-6 text-accentBlue mt-1" />
        </div>
      </header>

      {/* System status */}
      <section className="panel space-y-3">
        <h2 className="section-title">System Status</h2>
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-3">
            <div
              className={`h-3 w-3 rounded-full ${
                status === 'ok'
                  ? 'bg-statusSucceeded'
                  : status === 'unreachable'
                    ? 'bg-statusFailed'
                    : 'bg-statusQueued animate-pulse'
              }`}
            />
            <span className="text-sm text-textPrimary">
              {status === 'ok'
                ? 'Backend healthy'
                : status === 'unreachable'
                  ? 'Backend unreachable'
                  : 'Checking...'}
            </span>
          </div>
          {status === 'checking' && <span className="skeleton h-4 w-20 rounded" />}
        </div>
      </section>

      {/* Quick links */}
      <section className="panel space-y-3">
        <h2 className="section-title">Quick Links</h2>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <Link
            href="/items"
            className="group flex items-center gap-3 rounded-lg border border-borderWeak bg-surface p-4 text-sm text-textPrimary hover:bg-surfaceWeak hover:border-borderStrong hover:shadow-sm transition-all"
          >
            <ClipboardList className="h-5 w-5 text-accentBlue shrink-0" />
            <div>
              <p className="font-medium">Items</p>
              <p className="text-xs text-textPrimary">CRUD operations</p>
            </div>
          </Link>
          <Link
            href="/docs"
            className="group flex items-center gap-3 rounded-lg border border-borderWeak bg-surface p-4 text-sm text-textPrimary hover:bg-surfaceWeak hover:border-borderStrong hover:shadow-sm transition-all"
          >
            <BookOpen className="h-5 w-5 text-accentPurple shrink-0" />
            <div>
              <p className="font-medium">API Docs</p>
              <p className="text-xs text-textPrimary">OpenAPI specification</p>
            </div>
          </Link>
          <a
            href="/api/v1/health"
            className="group flex items-center gap-3 rounded-lg border border-borderWeak bg-surface p-4 text-sm text-textPrimary hover:bg-surfaceWeak hover:border-borderStrong hover:shadow-sm transition-all"
          >
            <HeartPulse className="h-5 w-5 text-accentPink shrink-0" />
            <div>
              <p className="font-medium">Health Check</p>
              <p className="text-xs text-textPrimary">Raw endpoint</p>
            </div>
          </a>
        </div>
      </section>

      {/* Getting Started */}
      <section className="panel space-y-3">
        <h2 className="section-title">Getting Started</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="flex items-start gap-3">
            <Code2 className="h-5 w-5 text-accentBlue shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-medium text-textPrimary">Build UI in app/</p>
              <p className="text-xs text-textPrimary mt-1">
                Next.js App Router with server and client components.
              </p>
            </div>
          </div>
          <div className="flex items-start gap-3">
            <Database className="h-5 w-5 text-accentGreen shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-medium text-textPrimary">Typed API Client</p>
              <p className="text-xs text-textPrimary mt-1">
                Auto-generated from OpenAPI spec. See{' '}
                <code className="text-textPrimary">lib/api/</code>.
              </p>
            </div>
          </div>
          <div className="flex items-start gap-3">
            <ClipboardList className="h-5 w-5 text-accentOrange shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-medium text-textPrimary">Reusable Components</p>
              <p className="text-xs text-textPrimary mt-1">
                Tested components in <code className="text-textPrimary">features/</code> for common
                patterns.
              </p>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
