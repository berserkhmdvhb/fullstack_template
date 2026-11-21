/**
 * The contrast backstop itself: it must flag what it claims to flag, or the a11y gate is hollow.
 *
 * @packageDocumentation
 */

import { expect, test } from '@playwright/test';

import { lowContrastText } from './support/contrast';

const page = (body: string): string =>
  `<!doctype html><body style="margin:0;background:#fff">${body}</body>`;

test('passes dark text on white', async ({ page: p }) => {
  await p.setContent(page('<p style="color:#111827">readable</p>'));
  expect(await lowContrastText(p)).toEqual([]);
});

test('flags white text on white', async ({ page: p }) => {
  await p.setContent(page('<p style="color:#fff">invisible</p>'));
  expect((await lowContrastText(p)).map((i) => i.text)).toEqual(['invisible']);
});

test('flags text faded by CSS opacity on the element', async ({ page: p }) => {
  await p.setContent(page('<p style="color:#111827;opacity:0.2">faded</p>'));
  expect((await lowContrastText(p)).map((i) => i.text)).toEqual(['faded']);
});

test('flags text faded by CSS opacity on an ancestor', async ({ page: p }) => {
  await p.setContent(page('<div style="opacity:0.2"><p style="color:#111827">faded</p></div>'));
  expect((await lowContrastText(p)).map((i) => i.text)).toEqual(['faded']);
});

test('reports a background image as unresolved, not as a pass', async ({ page: p }) => {
  await p.setContent(
    page(
      '<div style="background-image:linear-gradient(#000,#fff)"><p style="color:#111">on gradient</p></div>',
    ),
  );
  expect((await lowContrastText(p)).map((i) => i.ratio)).toEqual(['unresolved']);
});

test('skips disabled controls, which WCAG exempts', async ({ page: p }) => {
  await p.setContent(page('<button disabled style="color:#ccc;background:#fff">off</button>'));
  expect(await lowContrastText(p)).toEqual([]);
});
