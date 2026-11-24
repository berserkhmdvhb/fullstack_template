// The GitHub alert to Starlight aside mapping of src/lib/github-alerts.mjs.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { fromMarkdown } from 'mdast-util-from-markdown';
import { toString } from 'mdast-util-to-string';

import { remarkGithubAlerts } from '../src/lib/github-alerts.mjs';

const run = (markdown) => {
  const tree = fromMarkdown(markdown);
  remarkGithubAlerts()(tree);
  return tree.children;
};

test('each GitHub alert becomes the directive of its Starlight aside, titled as on GitHub', () => {
  const kinds = { NOTE: 'note', TIP: 'tip', IMPORTANT: 'note', WARNING: 'caution', CAUTION: 'danger' };
  for (const [kind, name] of Object.entries(kinds)) {
    const [node] = run(`> [!${kind}]\n> Read this.\n`);
    assert.equal(node.type, 'containerDirective', kind);
    assert.equal(node.name, name, kind);
    const [label, ...body] = node.children;
    assert.equal(label.data.directiveLabel, true);
    assert.equal(toString(label), kind[0] + kind.slice(1).toLowerCase());
    assert.equal(body.map((n) => toString(n)).join('\n'), 'Read this.', kind);
  }
});

test('the alert keeps its links and later paragraphs, and a marker alone on its line leaves no empty paragraph', () => {
  const [alert] = run('> [!TIP]\n>\n> See [the guide](./a.md).\n>\n> Second.\n');
  assert.equal(alert.type, 'containerDirective');
  const [, first, second] = alert.children;
  assert.equal(first.children[1].type, 'link');
  assert.equal(toString(second), 'Second.');
});

test('a plain blockquote, an unknown marker and a marker mid-text stay blockquotes', () => {
  for (const markdown of ['> Just a quote.\n', '> [!DANGER]\n> No such kind.\n', '> Text [!NOTE] here.\n']) {
    assert.equal(run(markdown)[0].type, 'blockquote', markdown);
  }
});

test('a marker followed by text on its line stays a blockquote, as on GitHub', () => {
  for (const markdown of ['> [!NOTE] Read this.\n', '> [!WARNING]text\n> More.\n']) {
    assert.equal(run(markdown)[0].type, 'blockquote', markdown);
  }
});
