# Documentation site

The curated reading path in front of the wiki: the architecture, the CI/CD pipeline, the design and the practices
adopted, plus the walkthroughs. Astro + Starlight, with Pagefind search built in (static, offline, no service).
Why it exists and why Starlight: [ADR-0041](../wiki/adr/0041-documentation-site.md).

## What lives where

| Where | What |
|---|---|
| [`wiki/`](../wiki/README.md) | The complete technical reference. A site page links into it rather than restating it. |
| `docs-site/src/content/docs/` | The site's own pages, written for the site. |
| [`CONTRIBUTING.md`](../CONTRIBUTING.md), [`wiki/cloudflare-setup.md`](../wiki/cloudflare-setup.md), [`wiki/gke-cluster-setup.md`](../wiki/gke-cluster-setup.md), [`wiki/github-pages-setup.md`](../wiki/github-pages-setup.md), [`wiki/pr-agent-setup.md`](../wiki/pr-agent-setup.md) | The walkthroughs. The site **loads** them from these files (`LOADED` in [`src/lib/repo.mjs`](src/lib/repo.mjs)); edit them there, never copy them. |
| [`site.json`](site.json) | The one repository value (owner/name and branch) every repository link and the Pages address come from. `infra/adapt` stamps it; CI passes the real repository in `SITE_REPOSITORY`, and the deploy passes the published Pages URL in `SITE_URL`. |

## Local preview

Use the pinned Node (`make tools`, then `.tools/bin` on `PATH`), from this directory:

```bash
npm ci --ignore-scripts               # the repository's install-scripts policy (wiki/frontend-deployment.md#install-scripts)
npx playwright install chromium       # once: the diagrams render at build time in Playwright's Chromium
npm run dev                           # live preview
npm test                              # the link resolver's unit tests (node:test)
npm run build && npm run test:a11y    # what CI runs: the strict build, then axe over every built page
```

`DOCS_SITE_PORT` moves the axe scan's preview server off its default port.

## Adding a page

1. Create `src/content/docs/<name>.md` with a `title` and a `description` in its front matter, and end it with
   "Go deeper" links into the wiki: a test fails a page without a description or a wiki link. Use `.mdx` only when
   the page needs one of Starlight's components (Steps, Tabs, Cards); in MDX, `<placeholder>` text needs a code
   span. Asides need no MDX: `:::note`, `:::tip`, `:::caution`, `:::danger`.
2. Add `<name>` to the `sidebar` in [`astro.config.mjs`](astro.config.mjs).
3. To show a repository file as a page instead, add it to `LOADED` in [`src/lib/repo.mjs`](src/lib/repo.mjs) and to
   the sidebar. It needs no front matter: the title is the file's H1. Keep it plain GitHub Markdown, so it reads
   the same on GitHub: its raw HTML renders as text, and a GitHub alert (`> [!NOTE]`, `> [!WARNING]` and the rest)
   becomes the matching aside ([`src/lib/github-alerts.mjs`](src/lib/github-alerts.mjs)).

A diagram is a fenced `mermaid` block. It is rendered at build time to inline SVG, once for the light and once for
the dark theme; the page ships no diagram JavaScript.

The site's pages give a short view and link the wiki for the rest: never restate a pinned version, a timeout, a
count or a command that lives elsewhere ([`tests/test_adr_index.py`](../tests/test_adr_index.py) checks the versions).

## Link rules

- Link any repository file with a **relative path** from the page's own file, as on GitHub (a page in
  `src/content/docs/` reaches the wiki four directories up; the pages show it). A file with a site page links to
  that page; any other file links to it on GitHub.
- The build fails on a link to a file Git does not track (a new file counts once Git sees it), on a `#anchor` that no
  heading of the target Markdown file has, and, through `starlight-links-validator`, on a broken internal link or
  anchor. Invalid front matter and a diagram that does not render fail it too.
- Never hard-code the site's base path or the repository URL: both follow `site.json`.

## Hosting

[`docs-site.yml`](../.github/workflows/docs-site.yml) publishes `dist/` to GitHub Pages from `main`
([CI/CD](../wiki/ci-cd.md#documentation-site)). The build is plain static files, so **Cloudflare Pages** can host the
same output instead: build command `npm ci --ignore-scripts && npx playwright install chromium && npm run build`, root
directory `docs-site`, output directory `dist`, the Node version from `mise.toml`, and `SITE_URL` set to the
address it is served at.

The build's `site` and `base` follow `SITE_URL`: the deploy passes the published URL (`html_url`) from the Pages
API, so a custom domain needs no change here. Without it (a pull request, or Pages off) they are
`https://<owner>.github.io/<repo>/`. Turning Pages on, the first deploy and a custom domain:
[GitHub Pages setup](../wiki/github-pages-setup.md).
