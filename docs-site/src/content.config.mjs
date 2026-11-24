// The docs collection: Starlight's own pages in src/content/docs/, plus the repository files in LOADED,
// read from where they live (one source per fact). A loaded page takes its title from the file's H1.
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

import { docsLoader } from '@astrojs/starlight/loaders';
import { docsSchema } from '@astrojs/starlight/schema';
import { defineCollection } from 'astro:content';

import { LOADED, ROOT, editUrl, splitTitle } from './lib/repo.mjs';

const pages = docsLoader();

const loader = {
  name: 'docs-and-repository-files',
  async load(context) {
    await pages.load(context);
    for (const [path, slug] of Object.entries(LOADED)) {
      const file = join(ROOT, path);
      const text = await readFile(file, 'utf8'); // a missing file fails the build
      const { title, body } = splitTitle(text, path);
      const data = await context.parseData({ id: slug, data: { title, editUrl: editUrl(path) } });
      // The slug front matter tells the link validator which page this file is.
      const rendered = await context.renderMarkdown(`---\nslug: ${slug}\n---\n${body}`, {
        fileURL: pathToFileURL(file),
      });
      context.store.set({ id: slug, data, body, rendered, digest: context.generateDigest(text) });
      context.watcher?.add(file);
    }
  },
};

export const collections = { docs: defineCollection({ loader, schema: docsSchema() }) };
