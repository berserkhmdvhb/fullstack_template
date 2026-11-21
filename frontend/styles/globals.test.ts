/**
 * Guard for the cascade layout of `globals.css` under Tailwind 4.
 *
 * @remarks
 * Tailwind 4 puts utilities in a cascade layer, and any unlayered rule beats a layered one whatever its
 * specificity (v3 compared specificity, so `.min-h-screen` beat an element rule like `html, body`).
 * A bare element selector outside a layer would therefore override utilities such as `min-h-screen`.
 *
 * @packageDocumentation
 */

import { readFileSync } from 'node:fs';
import path from 'node:path';

import postcss, { type AtRule, type Rule } from 'postcss';
import { describe, expect, it } from 'vitest';

const css = readFileSync(path.join(__dirname, 'globals.css'), 'utf8');
const root = postcss.parse(css);

/** At-rules whose children are not plain unlayered rules. */
const EXEMPT = new Set(['layer', 'theme', 'utility', 'custom-variant', 'keyframes', 'import']);

/**
 * Collect the rules that sit outside every cascade layer and Tailwind at-rule.
 *
 * @returns the unlayered rules
 */
function unlayeredRules(): Rule[] {
  const out: Rule[] = [];
  root.walkRules((rule) => {
    for (let p = rule.parent; p; p = p.parent) {
      if (p.type === 'atrule' && EXEMPT.has((p as AtRule).name)) return;
    }
    out.push(rule);
  });
  return out;
}

describe('globals.css cascade', () => {
  it('has no unlayered rule whose selector is a bare element (utilities must be able to override it)', () => {
    const bare = unlayeredRules().flatMap((r) =>
      r.selectors.filter((s) => !/[.#[:]/.test(s)).map((s) => s.trim()),
    );
    expect(bare).toEqual([]);
  });

  it('declares the element rules in @layer base', () => {
    const inBase: string[] = [];
    root.walkAtRules('layer', (layer) => {
      if (layer.params !== 'base') return;
      layer.walkRules((r) => inBase.push(...r.selectors.map((s) => s.trim())));
    });
    expect(inBase).toEqual(expect.arrayContaining(['html', 'body', 'header']));
  });

  it('has no theme entry that refers to a variable of its own name', () => {
    const self: string[] = [];
    root.walkAtRules('theme', (theme) => {
      theme.walkDecls((d) => {
        if (d.value.trim() === `var(${d.prop})`) self.push(d.prop);
      });
    });
    expect(self).toEqual([]);
  });
});
