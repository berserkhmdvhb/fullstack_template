/**
 * Accessibility feedback provider with aria-live region.
 *
 * @remarks
 * Renders a screen-reader-only `aria-live="polite"` region that announces
 * async action results to assistive technology users. Complements the
 * visual toast notification system by providing an auditory equivalent.
 *
 * Usage:
 * ```tsx
 * <FeedbackProvider>
 *   <App />
 * </FeedbackProvider>
 * ```
 *
 * Then call `useFeedback().announce('Operation completed')` from anywhere
 * in the tree to broadcast a message to screen readers.
 *
 * @packageDocumentation
 */

'use client';

import React, {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
  type PropsWithChildren,
} from 'react';

type FeedbackContextValue = {
  /**
   * Announce a message to screen readers via the aria-live region.
   *
   * @param message - The message to announce.
   */
  announce: (message: string) => void;
};

const FeedbackContext = createContext<FeedbackContextValue>({
  announce: () => {
    /* noop */
  },
});

/**
 * Hook to access the accessibility feedback system.
 *
 * @returns An object with an `announce` function.
 *
 * @public
 */
export function useFeedback(): FeedbackContextValue {
  return useContext(FeedbackContext);
}

/**
 * FeedbackProvider renders an aria-live region for screen reader announcements.
 *
 * @param props - Child components.
 * @returns Provider-wrapped subtree with aria-live portal.
 *
 * @public
 */
export function FeedbackProvider({ children }: PropsWithChildren): JSX.Element {
  const [message, setMessage] = useState<string>('');
  const counterRef = useRef(0);

  const announce = useCallback((msg: string) => {
    // Bump a counter to force re-render even if the message is the same
    counterRef.current += 1;
    setMessage(`${msg} (${counterRef.current})`);
  }, []);

  const value = { announce };

  return (
    <FeedbackContext.Provider value={value}>
      {children}
      {/* Screen-reader-only aria-live region */}
      <div aria-live="polite" aria-atomic="true" role="status" className="sr-only">
        {message}
      </div>
    </FeedbackContext.Provider>
  );
}
