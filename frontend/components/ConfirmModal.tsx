/**
 * Reusable confirmation modal dialog.
 *
 * @remarks
 * Replaces `window.confirm()` with an accessible, styled modal.
 * Uses a div-based overlay with role="dialog" for semantics.
 *
 * @packageDocumentation
 */

'use client';

import React, { useEffect, useRef } from 'react';

/**
 * ConfirmModal component.
 *
 * @remarks
 * - Renders a modal dialog with title, message, and confirm/cancel buttons
 * - Escape key closes the modal (via document keydown listener)
 * - Backdrop click closes the modal
 *
 * @param props - Modal properties.
 * @param props.isOpen - Whether the modal is visible.
 * @param props.title - Modal title.
 * @param props.message - Confirmation message.
 * @param props.confirmLabel - Label for the confirm button.
 * @param props.cancelLabel - Label for the cancel button.
 * @param props.onConfirm - Callback when confirm is clicked.
 * @param props.onCancel - Callback when cancel is clicked or modal is dismissed.
 *
 * @returns The confirmation modal.
 *
 * @public
 */
export function ConfirmModal({
  isOpen,
  title,
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  onConfirm,
  onCancel,
}: {
  isOpen: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
}): JSX.Element | null {
  const dialogRef = useRef<HTMLDivElement>(null);

  const handleBackdropClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) {
      onCancel();
    }
  };

  // Escape key closes the modal
  useEffect(() => {
    if (!isOpen) return;
    const handleKeydown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onCancel();
      }
    };
    document.addEventListener('keydown', handleKeydown);
    return () => document.removeEventListener('keydown', handleKeydown);
  }, [isOpen, onCancel]);

  if (!isOpen) return null;

  return (
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions, jsx-a11y/click-events-have-key-events -- Backdrop click is a standard modal pattern.
    <div
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-modal-title"
      onClick={handleBackdropClick}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
    >
      <div className="w-full max-w-md rounded-lg bg-surfaceStrong p-6 shadow-xl">
        <h3 id="confirm-modal-title" className="text-lg font-semibold text-textPrimary">
          {title}
        </h3>
        <p className="mt-2 text-sm text-textSecondary">{message}</p>

        <div className="mt-6 flex justify-end gap-3">
          <button
            type="button"
            onClick={onCancel}
            className="rounded px-4 py-2 text-sm text-textSecondary hover:bg-surfaceWeak"
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className="rounded bg-statusFailed px-4 py-2 text-sm font-medium text-white hover:bg-statusFailed/90"
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
