/**
 * Guard for the cascade layout of `globals.css` under Tailwind 4.
 *
 * @remarks
 * Tailwind 4 puts utilities in a cascade layer, and any unlayered rule beats a layered one whatever its
 * specificity (v3 compared specificity, so `.min-h-screen` beat an element rule like `html, body`).
 * A bare element selector outside a layer would therefore override utilities such as `min-h-screen`.
 *
 * The scope is element-only on purpose. An element rule (`html, body { ... }`) is the v3 habit that breaks silently
 * under v4. A selector with a class, id, attribute or other pseudo-class is a component style an author wrote
 * unlayered deliberately, so this guard does not police it. `:where(...)` and `:is(...)` add no specificity of their
 * own, so `:where(html)` is still a bare element and is unwrapped before the check.
 *
 * @packageDocumentation
 */

import { readFileSync } from 'node:fs';
import path from 'node:path';

import postcss, { type AtRule, type Container, type Document, type Rule } from 'postcss';
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
    for (let p: Container | Document | undefined = rule.parent; p; p = p.parent) {
      if (p.type === 'atrule' && EXEMPT.has((p as AtRule).name)) return;
    }
    out.push(rule);
  });
  return out;
}

/**
 * Whether a selector targets bare elements only, after unwrapping `:where(...)` and `:is(...)`.
 *
 * @param selector - one selector from a rule
 *
 * @returns true when no class, id, attribute or other pseudo-class remains
 */
function isBareElement(selector: string): boolean {
  let unwrapped = selector;
  for (let prev = ''; prev !== unwrapped;) {
    prev = unwrapped;
    unwrapped = unwrapped.replace(/:(?:where|is)\(([^()]*)\)/g, '$1');
  }
  return !/[.#[:]/.test(unwrapped);
}

describe('globals.css cascade', () => {
  it('has no unlayered rule whose selector is a bare element (utilities must be able to override it)', () => {
    const bare = unlayeredRules().flatMap((r) =>
      r.selectors.filter(isBareElement).map((s) => s.trim()),
    );
    expect(bare).toEqual([]);
  });

  it('declares the element rules in @layer base', () => {
    const inBase: string[] = [];
    root.walkAtRules('layer', (layer) => {
      if (layer.params !== 'base') return;
      layer.walkRules((r) => {
        inBase.push(...r.selectors.map((s) => s.trim()));
      });
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

describe('isBareElement', () => {
  it.each(['html', 'body', ':where(html)', ':is(html, body)', ':where(:is(header))'])(
    'flags %s as a bare element',
    (selector) => {
      expect(isBareElement(selector)).toBe(true);
    },
  );

  it.each(['.btn', '#root', 'a[href]', ':where(.btn)', 'html:not(.dark)', ':is(.a, .b)'])(
    'does not flag %s',
    (selector) => {
      expect(isBareElement(selector)).toBe(false);
    },
  );
});
