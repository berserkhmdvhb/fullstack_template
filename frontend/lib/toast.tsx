/**
 * Toast notification system.
 *
 * @remarks
 * Lightweight portal-based toast notifications with auto-dismiss.
 *
 * @packageDocumentation
 */

'use client';

import { X } from 'lucide-react';
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PropsWithChildren,
  type JSX,
} from 'react';

type ToastType = 'success' | 'error' | 'info';

type Toast = {
  id: string;
  type: ToastType;
  message: string;
};

type ToastContextValue = {
  notify: (type: ToastType, message: string) => void;
};

const ToastContext = createContext<ToastContextValue>({
  notify: () => {
    /* noop */
  },
});

/**
 * Hook to access the toast notification system.
 *
 * @returns An object with a `notify` function.
 *
 * @public
 */
export function useToast(): ToastContextValue {
  return useContext(ToastContext);
}

/**
 * ToastProvider renders a portal-based toast container.
 *
 * @param props - Child components.
 * @returns Provider-wrapped subtree with toast portal.
 *
 * @public
 */
export function ToastProvider({ children }: PropsWithChildren): JSX.Element {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const counterRef = useRef(0);

  const notify = useCallback((type: ToastType, message: string) => {
    const id = `toast-${++counterRef.current}`;
    setToasts((prev) => [...prev, { id, type, message }]);
  }, []);

  const remove = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  // Auto-dismiss toasts after 5 seconds
  useEffect(() => {
    if (toasts.length === 0) return;
    const timers = toasts.map((t) => setTimeout(() => remove(t.id), 5000));
    return () => {
      timers.forEach(clearTimeout);
    };
  }, [toasts, remove]);

  const value = useMemo(() => ({ notify }), [notify]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      {/* Portal target for toasts */}
      {typeof document !== 'undefined' && (
        <div className="fixed bottom-4 right-4 z-[200] flex flex-col gap-2">
          {toasts.map((t) => (
            <ToastItem key={t.id} toast={t} onDismiss={() => remove(t.id)} />
          ))}
        </div>
      )}
    </ToastContext.Provider>
  );
}

/**
 * Individual toast notification item.
 *
 * @param props - Toast data and dismiss callback.
 * @returns A styled toast notification.
 *
 * @internal
 */
function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: () => void }): JSX.Element {
  const bgClass =
    toast.type === 'success'
      ? 'bg-statusSucceeded/20 border-statusSucceeded/40 text-statusSucceeded'
      : toast.type === 'error'
        ? 'bg-statusFailed/20 border-statusFailed/40 text-statusFailed'
        : 'bg-accentBlue/20 border-accentBlue/40 text-accentBlue';

  return (
    <div
      role="alert"
      className={`flex items-center gap-3 rounded-lg border px-4 py-3 text-sm shadow-lg ${bgClass}`}
    >
      <span className="flex-1">{toast.message}</span>
      <button
        onClick={onDismiss}
        className="ml-2 text-current/60 hover:text-current"
        aria-label="Dismiss notification"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
