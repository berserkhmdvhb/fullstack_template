/**
 * Guards the MSW unhandled-request policy set in `setup.tsx`.
 *
 * @remarks
 * MSW 3 renamed `onUnhandledRequest` to `onUnhandledFrame`; the old key is ignored silently, which would turn
 * `bypass` into `warn`. An unmocked request must pass through to the network without an MSW warning.
 *
 * @packageDocumentation
 */

import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';

import type { Window as HappyWindow } from 'happy-dom';
import { vi } from 'vitest';

describe('MSW unhandled-request policy', () => {
  it('lets an unmocked request through without an MSW warning', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    // A real loopback server, made the page origin so the bypassed request is same-origin and succeeds
    const upstream = createServer((_req, res) => res.end('ok'));
    await new Promise<void>((resolve) => upstream.listen(0, '127.0.0.1', resolve));
    const { port } = upstream.address() as AddressInfo;
    const { happyDOM } = window as unknown as HappyWindow;
    const originalUrl = window.location.href;
    happyDOM.setURL(`http://127.0.0.1:${port}/`);

    try {
      const response = await fetch('/unmocked');

      expect(await response.text()).toBe('ok');
      expect(warn.mock.calls.flat().join(' ')).not.toMatch(/MSW/);
    } finally {
      warn.mockRestore();
      happyDOM.setURL(originalUrl);
      await new Promise((resolve) => upstream.close(resolve));
    }
  });
});
