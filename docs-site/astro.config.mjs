// The documentation site: Astro + Starlight, built to static HTML for GitHub Pages (ADR-0041).
import { unified } from '@astrojs/markdown-remark';
import starlight from '@astrojs/starlight';
import { defineConfig } from 'astro/config';
import rehypeMermaid from 'rehype-mermaid';
import starlightLinksValidator from 'starlight-links-validator';

import { remarkGithubAlerts } from './src/lib/github-alerts.mjs';
import { rehypeMermaidThemes } from './src/lib/mermaid-themes.mjs';
import { base, branch, remarkRepoLinks, repoUrl, site } from './src/lib/repo.mjs';

export default defineConfig({
  site,
  base,
  markdown: {
    // Astro 7 defaults to the satteri processor; these remark and rehype plugins need unified, hence the direct pin of @astrojs/markdown-remark.
    processor: unified({
      // Before Starlight's own plugins, which it appends: a GitHub alert becomes the directive its asides read.
      remarkPlugins: [remarkRepoLinks, remarkGithubAlerts],
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
      // The loaded repository files (LOADED) live outside src/content/docs; Starlight's asides and heading links
      // apply to them too.
      markdown: { processedDirs: ['..'] },
      sidebar: [
        { label: 'Start here', items: ['getting-started'] },
        { label: 'Overview', items: ['architecture', 'pipeline', 'security', 'design', 'practices', 'releasing'] },
        { label: 'Contribute', items: ['walkthroughs/contributing'] },
        {
          label: 'Set up the platform',
          items: ['setup/platform', 'setup/gke', 'setup/cloudflare', 'setup/github-pages', 'setup/pr-agent'],
        },
        // The site is a reading path; the wiki is the complete reference, one click from every page.
        { label: 'The wiki: the full reference', link: `${repoUrl}/blob/${branch}/wiki/README.md` },
      ],
      // Internal links and their #anchors; a broken one fails the build.
      plugins: [starlightLinksValidator()],
    }),
  ],
});
