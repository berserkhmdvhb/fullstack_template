// The repository this site documents, and the rules that turn a Markdown link into a site or GitHub link.
// astro.config.mjs and src/content.config.mjs both read it; docs-site/README.md explains the rules.
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join, posix, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import GithubSlugger from 'github-slugger';
import { fromMarkdown } from 'mdast-util-from-markdown';
import { toString } from 'mdast-util-to-string';

const SITE_DIR = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
export const ROOT = resolve(SITE_DIR, '..');
const DOCS_DIR = 'docs-site/src/content/docs/';

// One value: owner/name and the branch. infra/adapt stamps site.json; CI passes the real repository in
// SITE_REPOSITORY, as it passes SOURCE_URL to the image builds.
const config = JSON.parse(readFileSync(join(SITE_DIR, 'site.json'), 'utf8'));
export const repository = process.env.SITE_REPOSITORY || config.repository;
export const branch = config.branch;
const [owner, name] = repository.split('/');
export const repoUrl = `https://github.com/${repository}`;
// GitHub Pages: https://<owner>.github.io/<name>/, or the root for a repository named <owner>.github.io.
export const site = `https://${owner.toLowerCase()}.github.io`;
export const base = name.toLowerCase() === `${owner.toLowerCase()}.github.io` ? '/' : `/${name}`;

// The repository files the site renders as its own pages (loaded, never copied): path -> page slug.
export const LOADED = {
  'CONTRIBUTING.md': 'walkthroughs/contributing',
  'wiki/cloudflare-setup.md': 'walkthroughs/cloudflare',
  'wiki/gke-cluster-setup.md': 'walkthroughs/gke',
};

// Tracked files, plus new ones not yet added that Git does not ignore (so a page being written resolves).
const git = ['ls-files', '-z', '--cached', '--others', '--exclude-standard'];
const tracked = new Set(execFileSync('git', git, { cwd: ROOT, encoding: 'utf8' }).split('\0').filter(Boolean));
const isDir = (path) => [...tracked].some((f) => f.startsWith(`${path}/`));

const anchorCache = new Map();
/** The heading anchors GitHub (and the site) give a Markdown file: github-slugger over each heading's text. */
function anchors(path) {
  if (!anchorCache.has(path)) {
    const slugger = new GithubSlugger();
    const found = new Set();
    const walk = (node) => {
      if (node.type === 'heading') found.add(slugger.slug(toString(node)));
      node.children?.forEach(walk);
    };
    walk(fromMarkdown(readFileSync(join(ROOT, path), 'utf8')));
    anchorCache.set(path, found);
  }
  return anchorCache.get(path);
}

/** The site URL of a repository file, or undefined when the site has no page for it. */
function pageUrl(path) {
  const own = path.startsWith(DOCS_DIR) && path.endsWith('.md');
  const slug = LOADED[path] ?? (own ? path.slice(DOCS_DIR.length, -3).replace(/(^|\/)index$/, '') : undefined);
  return slug === undefined ? undefined : posix.join(base, slug, '/');
}

/**
 * Remark plugin: a relative link resolves against its file's place in the repository. A file with a site
 * page links to that page, any other tracked file to GitHub. A file that is not tracked, or a #anchor that
 * no heading of a Markdown target has, fails the build. In a loaded file, raw HTML renders as text, so a
 * `<placeholder>` stays visible (an HTML comment is dropped).
 */
export function remarkRepoLinks() {
  return (tree, file) => {
    const source = file.path && relative(ROOT, file.path).split('\\').join('/');
    if (!source || source.startsWith('..')) return;
    const loaded = source in LOADED;
    const broken = [];
    const visit = (node, parent, index) => {
      if (loaded && node.type === 'html') {
        if (/^\s*<!--/.test(node.value)) parent.children.splice(index, 1);
        else parent.children[index] = { type: 'text', value: node.value };
        return;
      }
      if ((node.type === 'link' || node.type === 'definition') && !/^([a-z][a-z0-9+.-]*:|\/|#)/i.test(node.url)) {
        const [rawPath, anchor] = node.url.split('#');
        const target = posix.normalize(posix.join(posix.dirname(source), decodeURI(rawPath))).replace(/\/$/, '');
        const where = `${source}: ${node.url}`;
        if (target.startsWith('..') || (!tracked.has(target) && !isDir(target))) {
          broken.push(`${where} (no such tracked file)`);
        } else if (anchor && target.endsWith('.md') && !anchors(target).has(anchor)) {
          broken.push(`${where} (no such heading in ${target})`);
        } else {
          const page = pageUrl(target);
          const kind = tracked.has(target) ? 'blob' : 'tree';
          node.url = (page ?? `${repoUrl}/${kind}/${branch}/${target}`) + (anchor ? `#${anchor}` : '');
        }
      }
      for (let i = (node.children?.length ?? 0) - 1; i >= 0; i--) visit(node.children[i], node, i);
    };
    visit(tree);
    if (broken.length) file.fail(`Broken repository links:\n  ${broken.join('\n  ')}`);
  };
}

/** The GitHub edit URL of a repository file. */
export const editUrl = (path) => `${repoUrl}/edit/${branch}/${path}`;
