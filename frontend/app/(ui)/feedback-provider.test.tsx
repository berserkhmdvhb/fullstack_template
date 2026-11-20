/**
 * Tests for accessibility feedback provider.
 *
 * @packageDocumentation
 */

import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { describe, expect, it } from 'vitest';

import { FeedbackProvider, useFeedback } from './feedback-provider';

describe('useFeedback', () => {
  it('should return announce function', () => {
    let announceFn: ((message: string) => void) | undefined;

    /** Test component that captures the announce function. */
    function TestComponent() {
      const { announce } = useFeedback();
      announceFn = announce;
      return null;
    }

    render(
      <FeedbackProvider>
        <TestComponent />
      </FeedbackProvider>,
    );

    expect(announceFn).toBeDefined();
    expect(typeof announceFn).toBe('function');
  });
});

describe('FeedbackProvider', () => {
  it('should render aria-live region', () => {
    render(
      <FeedbackProvider>
        <div>Test</div>
      </FeedbackProvider>,
    );

    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('should have aria-live polite', () => {
    render(
      <FeedbackProvider>
        <div>Test</div>
      </FeedbackProvider>,
    );

    const region = screen.getByRole('status');
    expect(region).toHaveAttribute('aria-live', 'polite');
  });

  it('should announce message', async () => {
    const { userEvent } = await import('@testing-library/user-event');

    /** Test component with a button that triggers announce. */
    function TestComponent() {
      const { announce } = useFeedback();
      return <button onClick={() => announce('Operation completed')}>Announce</button>;
    }

    render(
      <FeedbackProvider>
        <TestComponent />
      </FeedbackProvider>,
    );

    const user = userEvent.setup();
    await user.click(screen.getByText('Announce'));

    await waitFor(() => {
      const region = screen.getByRole('status');
      expect(region.textContent).toContain('Operation completed');
    });
  });
});
