/**
 * Tests for toast notification system.
 *
 * @packageDocumentation
 */

import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { describe, expect, it } from 'vitest';

import { ToastProvider, useToast } from './toast';

describe('useToast', () => {
  it('should return notify function', () => {
    let notifyFn: ((type: 'success' | 'error' | 'info', message: string) => void) | undefined;

    /** Test component that captures the notify function. */
    function TestComponent() {
      const { notify } = useToast();
      notifyFn = notify;
      return null;
    }

    render(
      <ToastProvider>
        <TestComponent />
      </ToastProvider>,
    );

    expect(notifyFn).toBeDefined();
    expect(typeof notifyFn).toBe('function');
  });
});

describe('ToastProvider', () => {
  it('should render toast when notify is called', async () => {
    /** Test component with a button that triggers a success toast. */
    function TestComponent() {
      const { notify } = useToast();
      return <button onClick={() => notify('success', 'Test message')}>Notify</button>;
    }

    render(
      <ToastProvider>
        <TestComponent />
      </ToastProvider>,
    );

    const { userEvent } = await import('@testing-library/user-event');

    const user = userEvent.setup();
    await user.click(screen.getByText('Notify'));

    await waitFor(() => {
      expect(screen.getByText('Test message')).toBeInTheDocument();
    });
  });

  it('should render toast with role alert', async () => {
    /** Test component with a button that triggers an info toast. */
    function TestComponent() {
      const { notify } = useToast();
      return <button onClick={() => notify('info', 'Alert message')}>Notify</button>;
    }

    render(
      <ToastProvider>
        <TestComponent />
      </ToastProvider>,
    );

    const { userEvent } = await import('@testing-library/user-event');

    const user = userEvent.setup();
    await user.click(screen.getByText('Notify'));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
  });

  it('should dismiss toast when dismiss button is clicked', async () => {
    const { userEvent } = await import('@testing-library/user-event');

    /** Test component with a button that triggers a dismissible toast. */
    function TestComponent() {
      const { notify } = useToast();
      return <button onClick={() => notify('info', 'Dismissible message')}>Notify</button>;
    }

    render(
      <ToastProvider>
        <TestComponent />
      </ToastProvider>,
    );

    const user = userEvent.setup();
    await user.click(screen.getByText('Notify'));

    // Click dismiss button
    const dismissButton = screen.getByLabelText('Dismiss notification');
    await user.click(dismissButton);

    expect(screen.queryByText('Dismissible message')).not.toBeInTheDocument();
  });
});
