// The documentation site: Astro + Starlight, built to static HTML for GitHub Pages (ADR-0041).
import { unified } from '@astrojs/markdown-remark';
import starlight from '@astrojs/starlight';
import { defineConfig } from 'astro/config';
import rehypeMermaid from 'rehype-mermaid';
import starlightLinksValidator from 'starlight-links-validator';

import { rehypeMermaidThemes } from './src/lib/mermaid-themes.mjs';
import { base, branch, remarkRepoLinks, repoUrl, site } from './src/lib/repo.mjs';

export default defineConfig({
  site,
  base,
  markdown: {
    // Astro 7 defaults to the satteri processor; these remark and rehype plugins need unified, hence the direct pin of @astrojs/markdown-remark.
    processor: unified({
      remarkPlugins: [remarkRepoLinks],
      // Build-time diagrams: inline SVG, no diagram JavaScript in the pages. A diagram that fails to render fails the build.
      rehypePlugins: [rehypeMermaidThemes, [rehypeMermaid, { strategy: 'inline-svg' }]],
    }),
  },
  integrations: [
    starlight({
      title: 'Service template',
      description: 'A FastAPI and Next.js service template: architecture, pipeline, design and practices.',
      social: [{ icon: 'github', label: 'GitHub', href: repoUrl }],
      editLink: { baseUrl: `${repoUrl}/edit/${branch}/docs-site/` },
      customCss: ['./src/styles/custom.css'],
      sidebar: [
        { label: 'Overview', items: ['architecture', 'pipeline', 'design', 'practices'] },
        {
          label: 'Walkthroughs',
          items: ['walkthroughs/contributing', 'walkthroughs/cloudflare', 'walkthroughs/gke'],
        },
      ],
      // Internal links and their #anchors; a broken one fails the build.
      plugins: [starlightLinksValidator()],
    }),
  ],
});
