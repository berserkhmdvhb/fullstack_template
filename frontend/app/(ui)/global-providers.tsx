/**
 * Client-side global providers wrapper.
 *
 * @remarks
 * Wraps the application subtree with React Query, Toast, and Feedback providers.
 * Must be a client component since it uses React hooks.
 *
 * @packageDocumentation
 */

'use client';

import React, { type PropsWithChildren } from 'react';

import { FeedbackProvider } from '@/app/(ui)/feedback-provider';
import { QueryProvider } from '@/lib/query';
import { ToastProvider } from '@/lib/toast';

/**
 * GlobalProviders wraps children with all required client-side providers.
 *
 * @param props - Child components.
 * @returns Provider-wrapped subtree.
 *
 * @public
 */
export function GlobalProviders({ children }: PropsWithChildren): JSX.Element {
  // useId provides a stable, unique key per component instance.
  const key = React.useId();

  return (
    <QueryProvider key={key}>
      <ToastProvider>
        <FeedbackProvider>{children}</FeedbackProvider>
      </ToastProvider>
    </QueryProvider>
  );
}
