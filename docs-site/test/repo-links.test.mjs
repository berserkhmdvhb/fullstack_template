// The link resolver, the H1 split and the Pages location of src/lib/repo.mjs, over real repository paths.
import assert from 'node:assert/strict';
import { join } from 'node:path';
import { test } from 'node:test';

import { fromMarkdown } from 'mdast-util-from-markdown';

import { LOADED, ROOT, base, branch, pagesLocation, remarkRepoLinks, repoUrl, splitTitle } from '../src/lib/repo.mjs';

const PAGE = join(ROOT, 'docs-site/src/content/docs/design.md');
const UP = '../../../../';

/** Run the resolver over `markdown` as the file at `path`; file.fail throws, as Astro's does. */
function resolve(markdown, path = PAGE) {
  const tree = fromMarkdown(markdown);
  const failures = [];
  const file = {
    path,
    fail(message) {
      failures.push(message);
      throw new Error(message);
    },
  };
  remarkRepoLinks()(tree, file);
  return { tree, failures };
}

const links = (tree) => {
  const found = [];
  const walk = (node) => {
    if (node.type === 'link') found.push(node.url);
    node.children?.forEach(walk);
  };
  walk(tree);
  return found;
};

test('a link to a file that is not tracked fails the build', () => {
  assert.throws(() => resolve(`[x](${UP}wiki/no-such-page.md)`), /no such tracked file/);
});

test('a missing heading anchor fails the build', () => {
  assert.throws(() => resolve(`[x](${UP}wiki/ci-cd.md#no-such-heading)`), /no such heading in wiki\/ci-cd\.md/);
});

test('a path that escapes the repository fails the build', () => {
  assert.throws(() => resolve(`[x](${UP}../outside.md)`), /no such tracked file/);
});

test('file.fail is called once with every broken link, and its throw stops the build', () => {
  const tree = fromMarkdown(`[a](${UP}wiki/nope-a.md) [b](${UP}wiki/nope-b.md)`);
  const calls = [];
  const file = {
    path: PAGE,
    fail(message) {
      calls.push(message);
      throw new Error('stopped');
    },
  };
  assert.throws(() => remarkRepoLinks()(tree, file), /stopped/);
  assert.equal(calls.length, 1);
  assert.match(calls[0], /nope-a\.md/);
  assert.match(calls[0], /nope-b\.md/);
});

test('a link to a site page resolves to that page', () => {
  const { tree, failures } = resolve('[p](./pipeline.md) [h](./index.mdx)');
  assert.deepEqual(failures, []);
  assert.deepEqual(links(tree), [`${base.replace(/\/$/, '')}/pipeline/`, `${base.replace(/\/$/, '')}/`]);
});

test('a link to a loaded file resolves to its page, with the anchor kept', () => {
  const { tree } = resolve(`[c](${UP}CONTRIBUTING.md) [g](${UP}wiki/gke-cluster-setup.md#111-public-or-private)`);
  const root = base.replace(/\/$/, '');
  assert.deepEqual(links(tree), [
    `${root}/contribute/contributing/`,
    `${root}/setup/gke/#111-public-or-private`,
  ]);
});

test('a link between guides is a site link; a guide anchor keeps its GitHub slug', () => {
  const guide = join(ROOT, 'wiki/platform-setup.md');
  const { tree } = resolve('[g](./gke-cluster-setup.md#24-production-in-its-own-project) [c](./ci-cd.md#live-drift)', guide);
  const root = base.replace(/\/$/, '');
  assert.deepEqual(links(tree), [
    `${root}/setup/gke/#24-production-in-its-own-project`,
    `${repoUrl}/blob/${branch}/wiki/ci-cd.md#live-drift`,
  ]);
});

test('the loader lists exactly CONTRIBUTING.md and the five setup guides', () => {
  assert.deepEqual(
    Object.entries(LOADED),
    [
      ['CONTRIBUTING.md', 'contribute/contributing'],
      ['wiki/platform-setup.md', 'setup/platform'],
      ['wiki/gke-cluster-setup.md', 'setup/gke'],
      ['wiki/cloudflare-setup.md', 'setup/cloudflare'],
      ['wiki/github-pages-setup.md', 'setup/github-pages'],
      ['wiki/pr-agent-setup.md', 'setup/pr-agent'],
    ],
  );
});

test('any other tracked file links to GitHub: blob/ for a file, tree/ for a directory', () => {
  const { tree } = resolve(`[m](${UP}Makefile) [w](${UP}wiki/ci-cd.md#caches) [a](${UP}wiki/adr/)`);
  assert.deepEqual(links(tree), [
    `${repoUrl}/blob/${branch}/Makefile`,
    `${repoUrl}/blob/${branch}/wiki/ci-cd.md#caches`,
    `${repoUrl}/tree/${branch}/wiki/adr`,
  ]);
});

test('in a loaded file, a <placeholder> stays as text and an HTML comment is dropped', () => {
  const { tree } = resolve('Run it in <your-project>.\n\n<!-- a note -->\n', join(ROOT, 'CONTRIBUTING.md'));
  assert.equal(tree.children.length, 1);
  const values = tree.children[0].children.map((node) => [node.type, node.value]);
  assert.deepEqual(values, [
    ['text', 'Run it in '],
    ['text', '<your-project>'],
    ['text', '.'],
  ]);
});

test('the title is the first depth-1 heading, never a # line in a code fence', () => {
  const text = '```bash\n# not a title\n```\n\n# The `real` title\n\nBody.\n';
  const { title, body } = splitTitle(text, 'x.md');
  assert.equal(title, 'The real title');
  assert.match(body, /# not a title/);
  assert.doesNotMatch(body, /real/);
  assert.throws(() => splitTitle('```\n# only in a fence\n```\n', 'y.md'), /y\.md: no H1/);
});

test('site and base come from the published URL when there is one, a custom domain included', () => {
  assert.deepEqual(pagesLocation('Acme/shop', 'https://docs.example.com/'), {
    site: 'https://docs.example.com',
    base: '/',
  });
  assert.deepEqual(pagesLocation('Acme/shop', 'https://acme.github.io/shop/'), {
    site: 'https://acme.github.io',
    base: '/shop',
  });
});

test('without a published URL, site and base are the github.io derivation', () => {
  assert.deepEqual(pagesLocation('Acme/shop', ''), { site: 'https://acme.github.io', base: '/shop' });
  assert.deepEqual(pagesLocation('Acme/acme.github.io', undefined), { site: 'https://acme.github.io', base: '/' });
});

test('an .mdx site page resolves to its page like an .md one', () => {
  const { tree, failures } = resolve('[s](./getting-started.mdx)');
  assert.deepEqual(failures, []);
  assert.deepEqual(links(tree), [`${base.replace(/\/$/, '')}/getting-started/`]);
});
