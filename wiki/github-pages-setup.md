# GitHub Pages setup: publish the documentation site

This guide publishes the [documentation site](../docs-site/README.md) to GitHub Pages. It covers:
- turning Pages on with **Source: GitHub Actions**, through the settings script or by hand;
- the `github-pages` environment, the first deploy and the site's address;
- an optional **custom domain** on Cloudflare DNS;
- a repository that already publishes Pages from a branch;
- what each notice of the deploy run means.

The deploy itself is [`docs-site.yml`](../.github/workflows/docs-site.yml); how it is built and guarded is in
[CI/CD, Documentation site](./ci-cd.md#documentation-site), and why the site exists in
[ADR-0041](./adr/0041-documentation-site.md). [Platform Setup](./platform-setup.md) gives the overall order: do this
after the first push.

Every step ends with a **Check**. Don't move on until it passes.

| | |
|---|---|
| Time | 10 minutes, plus up to an hour for a custom domain's certificate |
| Cost | none on a public repository. A private repository needs a paid plan for Pages |
| You need | admin rights on the repository, `gh` signed in as that admin, and `curl` and `dig` for the checks |
| Written | 2026-11-24. GitHub's settings pages move around, so if a path has changed, search the settings for the name. |

## Contents

1. [What you will build](#1-what-you-will-build)
2. [Turn Pages on with Source: GitHub Actions](#2-turn-pages-on-with-source-github-actions)
3. [The github-pages environment](#3-the-github-pages-environment)
4. [The first deploy and the address](#4-the-first-deploy-and-the-address)
5. [Optional: a custom domain](#5-optional-a-custom-domain)
6. [A site that already builds from a branch](#6-a-site-that-already-builds-from-a-branch)
7. [The deploy notices](#7-the-deploy-notices)

---

## 1. What you will build

> **Do now.**

```text
 push to main that changes the site's inputs (or a manual run)
    │
    ▼
 docs-site.yml
    pages   ── is Pages on, with Source: GitHub Actions? reads its published address
    build   ── the strict build and the axe scan, then the Pages artifact
    deploy  ── the github-pages environment, main only: publishes the artifact
    │
    ▼
 https://<owner>.github.io/<repo>/   (or your custom domain)
```

**Why this design:**
- **No token to keep.** The deploy proves who it is to Pages with the run's own OIDC token; only the `deploy` job holds
  `pages: write`, and only in the `github-pages` environment.
- **Green until you opt in.** While Pages is off or builds from a branch, the run deploys nothing and says why, so a
  fresh copy of the template stays green.
- **One address, read from Pages.** The build takes the site's address from the Pages API, so a custom domain needs
  no change in the repository.

## 2. Turn Pages on with Source: GitHub Actions

> **Do now.**

The settings script does it: its `pages()` function creates the Pages site with `build_type: workflow` when there is
none ([`scripts/github-settings.sh`](../scripts/github-settings.sh)). Run the script as
[GitHub repository settings, Run it](./github-repository-settings.md#run-it) says; the dry run shows the call as
`POST /repos/<owner>/<repo>/pages`.

By hand instead: **Settings → Pages → Build and deployment → Source: GitHub Actions**
([Configuring a publishing source](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)).

On a private repository, Pages needs GitHub Pro, Team or Enterprise. When the plan refuses it, the script records a
skip and exits 3; the site of a private repository is still public unless the plan offers private Pages
([GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/about-github-pages)).

**Check:**

```bash
gh api repos/<owner>/<repo>/pages --jq '.build_type'
# → workflow
```

If it prints `legacy`, Pages builds from a branch: see [section 6](#6-a-site-that-already-builds-from-a-branch).

## 3. The github-pages environment

> **Do now.**

The deploy job runs in the `github-pages` environment. The settings script creates it and keeps one deployment rule
on it, **branch** `main`, so no other branch can publish; any other rule is deleted
([What the script sets](./github-repository-settings.md#what-the-script-sets)). GitHub adds a rule of its own when
Pages is created; the script sets the environment's rules itself rather than relying on that one. Add no secret and
no reviewer to it: the deploy needs neither, and a reviewer would hold every deploy.

**Check:**

```bash
gh api repos/<owner>/<repo>/environments/github-pages/deployment-branch-policies --jq '.branch_policies[] | "\(.type) \(.name)"'
# → branch main
```

Run it again after the first deploy (section 4): the rule must still be `branch main` alone. If GitHub reset it,
re-run the settings script with `--apply`.

## 4. The first deploy and the address

> **Do now.**

The next push to `main` that changes the site's inputs (the `paths:` of
[`docs-site.yml`](../.github/workflows/docs-site.yml)) deploys. To deploy now, without a push:

```bash
gh workflow run docs-site.yml --ref main
gh run list --workflow docs-site.yml --limit 1   # then follow it under Actions, or with gh run watch
```

The address is the one the Pages API reports as `html_url`: `https://<owner>.github.io/<repo>/`, or the root of
`https://<owner>.github.io/` for a repository named `<owner>.github.io`. The `deploy` job shows it as the environment
URL in the run's summary ([Using custom workflows with GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)).

**Check:** all three jobs are green, and the site answers at its address:

```bash
url="$(gh api repos/<owner>/<repo>/pages --jq '.html_url')"
curl -s -o /dev/null -w '%{http_code}\n' "$url"
# → 200
```

Open it, run a search, switch the theme, and open a page with a diagram: the diagram must follow the theme.

## 5. Optional: a custom domain

> **Managed as code** for the DNS record: an infrastructure-as-code repository may create it. The Pages settings
> are **Do now**.

This serves the site at a subdomain such as `docs.<domain>`, with the zone on Cloudflare
([Cloudflare setup](./cloudflare-setup.md)). Nothing in the repository changes: the next deploy reads the new
address from the Pages API and builds for it ([`docs-site/README.md`](../docs-site/README.md#hosting)).

### 5.1 Verify the domain with GitHub

Verification stops anyone else from pointing a Pages site at your domain. Under your **profile's Settings → Pages**
(or the organisation's **Settings → Pages**), **Add a domain**, enter `<domain>`, and add the TXT record GitHub shows
(`_github-pages-challenge-<owner>.<domain>`) in Cloudflare under **DNS → Records**. Then click **Verify**
([Verifying your custom domain](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/verifying-your-custom-domain-for-github-pages)).

**Check:** the domain shows as verified on that page.

### 5.2 The DNS record, DNS only

In Cloudflare, **DNS → Records → Add record**:

| Type | Name | Target | Proxy status |
|---|---|---|---|
| CNAME | `docs` | `<owner>.github.io` | **DNS only** (grey cloud) |

DNS only first: GitHub checks that the name resolves to it before it issues the site's certificate, and Cloudflare's
proxy would answer in its place ([Proxy status](https://developers.cloudflare.com/dns/proxy-status/)). A subdomain
needs only this record; an apex domain needs the records in GitHub's guide below.

**Check:**

```bash
dig +short CNAME docs.<domain>
# → <owner>.github.io.
```

### 5.3 The Pages custom domain and Enforce HTTPS

1. **Settings → Pages → Custom domain**: enter `docs.<domain>` and **Save**. GitHub runs a DNS check.
2. Wait until the certificate is issued; the page then offers **Enforce HTTPS**. Tick it
   ([Securing your site with HTTPS](https://docs.github.com/en/pages/getting-started-with-github-pages/securing-your-github-pages-site-with-https)).
3. Deploy again (`gh workflow run docs-site.yml --ref main`): the site that is live was built for the old address,
   and only a new build takes the new one.

A Pages site built by GitHub Actions needs no `CNAME` file in the artifact; the setting is the source
([Managing a custom domain](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/managing-a-custom-domain-for-your-github-pages-site)).

**Check:**

```bash
gh api repos/<owner>/<repo>/pages --jq '.html_url, .https_enforced'
# → https://docs.<domain>/
# → true
curl -s -o /dev/null -w '%{http_code}\n' https://docs.<domain>/
# → 200
```

To remove the domain, clear it under **Settings → Pages** and deploy again; then delete the DNS record.

## 6. A site that already builds from a branch

> **Do now.**

A repository may already publish Pages from a branch (a `gh-pages` Helm index, say). The settings script leaves such
a site as it is: it records a skip that names the build type and exits 3, and `docs-site.yml` deploys nothing, with a
notice ([section 7](#7-the-deploy-notices)).

Switching the source to GitHub Actions **stops the branch publishing**: whatever the branch served disappears from
the address. Decide which one the address is for:

- **Keep the branch site:** do nothing. The docs site still builds and is checked on every pull request; it is only
  not published. Another host can serve the same build ([`docs-site/README.md`](../docs-site/README.md#hosting)).
- **Publish the docs site instead:** move what the branch served elsewhere first, then set **Settings → Pages →
  Source** to **GitHub Actions** and deploy (section 4).

**Check:** `gh api repos/<owner>/<repo>/pages --jq '.build_type'` prints the source you chose.

## 7. The deploy notices

> **Do now.**

The `pages` job of [`docs-site.yml`](../.github/workflows/docs-site.yml) leaves one of these in the run's summary:

| Notice | Means | Do |
|---|---|---|
| none, and three green jobs | Pages builds from GitHub Actions; the site was deployed | nothing |
| **Docs site not deployed**: GitHub Pages is not enabled | the Pages API answered 404: Pages is off | [section 2](#2-turn-pages-on-with-source-github-actions) |
| **Docs site not deployed**: GitHub Pages builds from a branch | Pages is on, with a branch as its source | [section 6](#6-a-site-that-already-builds-from-a-branch) |
| **Docs site not deployed** (a warning): the Pages API returned an address that is not a plain `https://` URL | nothing was deployed and the live site is unchanged; a custom domain may report `http://` until its certificate is issued and **Enforce HTTPS** is on | finish [section 5.3](#53-the-pages-custom-domain-and-enforce-https), then deploy again |
| the `pages` job fails with the API's error | any other Pages API error (a 5xx, a refused token) | re-run the job; if it persists, read the error |
| the `deploy` job is refused by the environment | the `github-pages` deployment rule does not admit the ref | [section 3](#3-the-github-pages-environment) |

A run in a fork, or on any ref but `main`, skips every job: only `main` deploys.
