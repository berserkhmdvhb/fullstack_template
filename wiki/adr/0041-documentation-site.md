# ADR-0041: A curated documentation site, built with Starlight and published to GitHub Pages

## Status

Accepted.

## Date

2026-11-24

## Context

The wiki is the complete technical reference: many pages, written for someone changing the template. A reader
who wants the architecture, the pipeline, the overall design or the practices adopted has no reading path through
it, and no diagrams beside the text. The repository is public, so a site may link any tracked file.

The docs use `<placeholder>` angle brackets throughout, and every version lives in one pinned place
([Conventions](../../CONVENTIONS.md#3-one-source-of-truth-single-source--dry)). A site must not become a second
copy of the wiki that drifts from it.

## Decision

[`docs-site/`](../../docs-site/README.md) is an Astro + Starlight site (MIT), with Pagefind search built in (static,
offline, no service). It is **curated**: new pages for the architecture, the pipeline, the design and the practices,
each linking into the wiki rather than restating it. The detailed walkthroughs are the exception: the contributing
guide and the Cloudflare, GKE, GitHub Pages and PR-Agent setup guides are **loaded from their files** by a
content-collection loader, never copied, and render as Markdown (not MDX), so their angle brackets stay text and they
still read the same on GitHub.

- One repository value (owner/name and branch) builds every link into the repository and the site's Pages
  address. `infra/adapt` stamps it; CI passes the real repository.
- The build is strict: a link to a file that is not tracked, a missing `#anchor`, a broken internal link, invalid
  front matter or a diagram that fails to render fails it.
- Mermaid diagrams render at build time to inline SVG, a light and a dark copy, in the Playwright Chromium the
  repository already pins. The pages ship no diagram JavaScript.
- The site is pinned like every other tool: exact versions, a committed lockfile, the 7-day release age, and no
  dependency install scripts.
- [`docs-site.yml`](../../.github/workflows/docs-site.yml) publishes to **GitHub Pages** with the official Pages
  actions, from `main` only. Its deploy job alone holds `pages: write` and `id-token: write`, in the `github-pages`
  environment.

The folder is `docs-site/`, not `docs/`: a `docs/` beside `wiki/` would leave an adopter asking which one is the
documentation.

## Consequences

- Good: a reader gets a short, visual path, and the wiki stays the one reference.
- Good: the walkthroughs exist once; a link that breaks fails the pull request, not the reader.
- Bad: a second Node project to keep current (Renovate groups Astro and Starlight).
- Bad: the build needs Chromium, so the CI job installs it, like the frontend's axe job.

## Alternatives considered

- **Zensical**, the successor of Material for MkDocs: promising, but young; its plugin and link-validation story was
  not settled when this was decided.
- **MkDocs 1.x with Material 9.7**: mature, but Material is in maintenance mode, so it is a dead end for a new site.
- **Docusaurus**: parses Markdown as MDX, where the docs' `<placeholder>` angle brackets are JSX and break the build.
- **Cloudflare Pages** instead of GitHub Pages: it serves the same static build, and the template documents it as an
  alternative host. GitHub Pages is the default because it needs no account or token beyond the repository
  ([ADR-0001](./0001-github-only.md)).
- **A mirror of the whole wiki**: every wiki page rendered as a site page. It doubles the surface without a reading
  path, and the wiki already renders on GitHub.

## Links

- [`docs-site/README.md`](../../docs-site/README.md)
- [CI/CD: the documentation site](../ci-cd.md#documentation-site)
- [ADR-0001: GitHub only](./0001-github-only.md)
