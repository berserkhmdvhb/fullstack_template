/**
 * Tests for the root layout: its metadata, the skip link and the children inside the providers.
 *
 * @remarks
 * The layout renders `<html>`, so it is rendered to a string. `next/headers` is mocked to supply the CSP nonce
 * that `proxy.ts` forwards as `x-nonce`.
 *
 * @packageDocumentation
 */

import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';

const requestHeaders = vi.hoisted(() => ({ nonce: 'abc123' as string | null }));

vi.mock('next/headers', () => ({
  headers: () =>
    Promise.resolve(new Headers(requestHeaders.nonce ? { 'x-nonce': requestHeaders.nonce } : {})),
}));

import RootLayout, { metadata } from './layout';

describe('RootLayout', () => {
  it('names the service in the page metadata', () => {
    expect(metadata.title).toBe('Your Service Name');
    expect(metadata.description).toContain('Your Service Name');
  });

  it('renders its children in the main landmark, after a skip link', async () => {
    const view = renderToStaticMarkup(await RootLayout({ children: <p>page body</p> }));

    expect(view).toContain('<html lang="en"');
    expect(view).toContain('href="#main"');
    expect(view).toMatch(/<main id="main"[^>]*><p>page body<\/p><\/main>/);
  });

  it('mirrors the CSP nonce onto the html element when the proxy sent one', async () => {
    requestHeaders.nonce = 'abc123';
    const view = renderToStaticMarkup(await RootLayout({ children: null }));

    expect(view).toContain('nonce="abc123"');
  });

  it('renders without a nonce when no header is present', async () => {
    requestHeaders.nonce = null;
    const view = renderToStaticMarkup(await RootLayout({ children: null }));

    expect(view).not.toContain('nonce=');
  });
});
