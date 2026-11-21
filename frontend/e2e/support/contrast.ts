/**
 * A deterministic contrast backstop for the axe gate.
 *
 * @remarks
 * axe files text it cannot resolve (for example white text on a transparent button over a white
 * card) under `incomplete`, not `violations`. This helper measures every visible text node
 * from the browser's computed styles: the page is painted from `<html>` down to the text over white, each element's
 * background and CSS `opacity` applied as a group (so a faded ancestor fades its own background too), and the text
 * colour is compared with the same paint without the text. A background image or gradient on an ancestor makes the
 * answer unknowable, so that node is reported as `unresolved` instead of passing. Limits: it ignores element
 * geometry (a background that does not cover the text still counts), blend modes and filters.
 *
 * @packageDocumentation
 */

import type { Page } from '@playwright/test';

/** One text node that fails the WCAG 1.4.3 ratio, or whose background cannot be resolved. */
export type TextContrastIssue = {
  text: string;
  element: string;
  ratio: number | 'unresolved';
  needed: number;
};

/** Visible text whose contrast is below 4.5:1 (3:1 for large text), measured in the page. */
export async function lowContrastText(page: Page): Promise<TextContrastIssue[]> {
  return page.evaluate(() => {
    const canvas = document.createElement('canvas');
    canvas.width = 1;
    canvas.height = 1;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    if (!ctx) throw new Error('no 2d canvas to resolve CSS colours');

    /** Any CSS colour (rgb, oklab, color-mix, ...) as [r, g, b, a]. */
    const rgba = (css: string): [number, number, number, number] => {
      ctx.clearRect(0, 0, 1, 1);
      ctx.fillStyle = '#000';
      ctx.fillStyle = css;
      ctx.fillRect(0, 0, 1, 1);
      const [r = 0, g = 0, b = 0, a = 0] = ctx.getImageData(0, 0, 1, 1).data;
      return [r, g, b, a / 255];
    };
    const over = (top: number[], below: number[]): number[] => {
      const a = top[3] ?? 1;
      return [0, 1, 2].map((i) => (top[i] ?? 0) * a + (below[i] ?? 0) * (1 - a)).concat([1]);
    };
    const lum = (c: number[]): number => {
      const [r = 0, g = 0, b = 0] = c.slice(0, 3).map((v) => {
        const s = v / 255;
        return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    const ratio = (a: number[], b: number[]): number => {
      const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x) as [number, number];
      return (hi + 0.05) / (lo + 0.05);
    };
    const label = (el: Element): string =>
      `${el.tagName.toLowerCase()}${el.id ? `#${el.id}` : ''}${
        typeof el.className === 'string' && el.className
          ? `.${el.className.trim().split(/\s+/).join('.')}`
          : ''
      }`;

    const issues: TextContrastIssue[] = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const text = node.textContent?.trim();
      const el = node.parentElement;
      if (!text || !el || ['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(el.tagName)) continue;
      if (!el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      const box = range.getBoundingClientRect();
      if (box.width < 2 || box.height < 2) continue;
      if (el.closest('[disabled], [aria-disabled="true"]')) continue; // inactive controls are exempt

      // The chain from <html> down to the text element, each with its background and its opacity.
      let unresolved = false;
      const chain: Array<{ bg: number[]; opacity: number }> = [];
      for (let up: Element | null = el; up; up = up.parentElement) {
        const style = getComputedStyle(up);
        if (style.backgroundImage !== 'none') unresolved = true;
        chain.unshift({
          bg: rgba(style.backgroundColor),
          opacity: Number.parseFloat(style.opacity),
        });
      }

      const style = getComputedStyle(el);
      const size = Number.parseFloat(style.fontSize);
      const bold = Number.parseInt(style.fontWeight, 10) >= 700;
      const needed = size >= 24 || (bold && size >= 18.66) ? 3 : 4.5;
      if (unresolved) {
        issues.push({ text: text.slice(0, 40), element: label(el), ratio: 'unresolved', needed });
        continue;
      }
      // Paint the chain over a white page. An element with `opacity` is a group: its background and everything
      // inside it are blended with what is underneath, so a faded ancestor fades its own background too.
      const textColor = rgba(style.color);
      const paint = (index: number, base: number[], withText: boolean): number[] => {
        const node = chain[index];
        if (!node) return withText ? over(textColor, base) : base;
        const inner = paint(index + 1, over(node.bg, base), withText);
        return [0, 1, 2]
          .map((i) => (base[i] ?? 0) * (1 - node.opacity) + (inner[i] ?? 0) * node.opacity)
          .concat([1]);
      };
      const backdrop = paint(0, [255, 255, 255, 1], false);
      const fg = paint(0, [255, 255, 255, 1], true);
      const measured = Math.round(ratio(fg, backdrop) * 100) / 100;
      if (measured < needed) {
        issues.push({ text: text.slice(0, 40), element: label(el), ratio: measured, needed });
      }
    }
    return issues;
  });
}
