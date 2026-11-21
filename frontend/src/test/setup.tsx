/**
 * Test environment setup.
 *
 * @remarks
 * Configures the test environment with:
 * - Mocked Next.js components (next/image, next/dynamic, next/navigation)
 * - Custom render utility with all providers pre-wired
 * - MSW server for API mocking
 *
 * This file is automatically loaded by Vitest via the `setupFiles` config.
 *
 * @packageDocumentation
 */

import '@testing-library/jest-dom';
import { vi } from 'vitest';

import { server } from './mocks/server';

// Mock next/image
vi.mock('next/image', () => ({
  default: function MockedImage({
    src,
    alt,
    ...props
  }: {
    src: string;
    alt: string;
    [key: string]: unknown;
  }) {
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={src} alt={alt} {...props} />;
  },
}));

// Mock next/dynamic (return component directly, no lazy loading)
vi.mock('next/dynamic', () => ({
  default: (imported: () => Promise<{ default: React.ComponentType<unknown> }>) => {
    let Component: React.ComponentType<unknown> | null = null;
    const DynamicComponent = (props: Record<string, unknown>) => {
      if (!Component) {
        return <div data-testid="dynamic-loading" />;
      }
      return <Component {...props} />;
    };
    void imported().then((mod) => {
      Component = mod.default;
    });
    DynamicComponent.displayName = 'DynamicComponent';
    return DynamicComponent;
  },
}));

// Mock next/navigation
vi.mock('next/navigation', () => ({
  useRouter() {
    return {
      push: vi.fn(),
      replace: vi.fn(),
      prefetch: vi.fn(),
      back: vi.fn(),
      forward: vi.fn(),
      refresh: vi.fn(),
    };
  },
  usePathname() {
    return '/';
  },
  useSearchParams() {
    return new URLSearchParams();
  },
  useParams() {
    return {};
  },
  redirect(url: string) {
    throw new Error(`NEXT_REDIRECT ${url}`);
  },
  permanentRedirect(url: string) {
    throw new Error(`NEXT_PERMANENT_REDIRECT ${url}`);
  },
  notFound() {
    throw new Error('NEXT_NOT_FOUND');
  },
}));

// Start MSW server before all tests
beforeAll(() => server.listen({ onUnhandledFrame: 'bypass' }));

// Reset handlers after each test
afterEach(() => server.resetHandlers());

// Clean up after all tests
afterAll(() => server.close());
