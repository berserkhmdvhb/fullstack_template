# Template Upgrades

How a repository adopted from this template takes in later template releases. There is no generator: an adopted
repository is ordinary Git, and an upgrade is a merge or a cherry-pick from the template, guided by the changelog's
Upgrading list ([ADR-0039](./adr/0039-copier-deferred.md)).

---

## Table of Contents

- [The recorded version](#the-recorded-version)
- [Upgrading](#upgrading)
- [Conflicts with the renamed placeholders](#conflicts-with-the-renamed-placeholders)
- [When Copier would be the better tool](#when-copier-would-be-the-better-tool)

---

## The recorded version

`.template-version` holds one line, the template release the repository was made from, as its tag (`v<version>`).
`infra/adapt/adopt.sh` writes it at adoption from `.release-please-manifest.json`, the file release-please keeps at the
last release it cut, so the template carries no second version literal and does not ship the file. The record is
written once: a re-run keeps it, and you move it yourself after each upgrade. It is a separate file because your own
releases take the manifest over.

The template's releases are its `v*` tags, cut by release-please; its first tag is its first public release. Make a
copy from a release, or from `main` after one: nothing before the first release is owed to an adopter, so the
adopter actions start there. A copy made from `main` between releases holds that release plus later commits: the
upgrade below then re-applies a few changes you already have, which merge cleanly or come out empty.

---

## Upgrading

1. **Compare the versions.** Read your `.template-version`, then the template's [Changelog](./changelog.md): every
   release after yours, and `[Unreleased]` if you take `main`.
2. **Read the adopter actions.** Each release's **Upgrading** list gathers the steps a running deployment needs (a
   bootstrap re-run, a settings re-run, a new value). From the first release on, every other entry says "Nothing to
   do when adopting" or links that list ([CONTRIBUTING](../CONTRIBUTING.md#appendix-a-pull-request-checklist-copy-paste)
   requires it). Note the steps before you merge: some must be done before the next deploy.
3. **Bring the changes in** from a template remote, on a branch:

   ```bash
   git remote add upstream https://github.com/<template-owner>/<template-repo>.git
   # The template's v* tags must not land among your own: your release-please tags are v* too, and setuptools_scm
   # reads the version from `git describe`. Keep them under the remote instead.
   git config remote.upstream.tagOpt --no-tags
   git fetch upstream '+refs/tags/v*:refs/remotes/upstream/tags/v*'
   git switch -c template-upgrade
   # A fork or clone shares the template's history: merge the release.
   git merge upstream/tags/v<new>
   # A copy made with "Use this template" has no shared history: replay the template's commits between the releases
   # (each is a three-way merge against its own parent), without the template's release commits.
   git cherry-pick $(git rev-list --reverse --invert-grep --grep='^chore(main): release' \
     upstream/tags/v<old>..upstream/tags/v<new>)
   ```

   `v<old>` is your `.template-version`, a real template tag from the first release on. The `upstream/tags/*` refs are
   remote-tracking refs: never push them (`git push --tags` does not, and nothing else should). For a few changes
   only, find their commits with `git log --oneline upstream/tags/v<old>..upstream/tags/v<new> -- <path>` and
   cherry-pick those.
4. **Re-apply the adoption** to what came in: see [Conflicts](#conflicts-with-the-renamed-placeholders) below.
5. **Record the new version:** write `v<new>` to `.template-version` in the same pull request.
6. **Run the gates:** `make check-all-dry`, `make test` and `make precommit` locally, then the pull request's CI
   (`CI OK`). Do the Upgrading steps you noted, in the order the list gives.

---

## Conflicts with the renamed placeholders

**Files your copy owns.** Keep your own `.release-please-manifest.json`, `.release-please-version` and
`wiki/changelog.md` (your releases and their notes), and `.template-version` apart from the new version you write
into it. On a conflict in one of them take yours (`git checkout --ours <file>`); a merge that brought template
changes into one cleanly is undone with `git checkout HEAD -- <file>` before you commit. The release commits the
cherry-pick above skips are the template's `chore(main): release …` commits, which touch only these files.

The adapter rewrote the placeholders in your copy (the service name, the domain, the registry, the repository and the
code owner; [Adaptation](./adaptation.md#config-driven-adoption-kubernetes-deployments)), and the template still has
them, so a template change to one of those lines conflicts, and a new file arrives with the placeholders in it.

- **On a conflict**, keep the template's line, placeholder included, and your own change beside it if you made one.
  Resolve every file before the next step.
- **Then re-run the adapter** over what came in. `infra/adapt/adopt.sh` re-stamps the domain, registry, repository and
  code owner from your `adopt.conf` (it skips files that hold none), and keeps `.template-version`. The service name
  needs the old name given, because the adapter detects your current one:
  `infra/adapt/replace_service_name.sh --new-name <your_name> --old-name your_service_name --dry-run` first, then
  without `--dry-run`. A renamed path (`src/your_service_name/…`, the chart directory) comes in under the
  placeholder name: the rename moves it.
- **Check the placeholder package is gone:** `src/your_service_name/` must not exist after the rename. While it exists
  the next check takes the repository for the template and passes without checking anything, which is exactly what a
  skipped rename leaves.
- **Check that nothing is left:** `.tools/bin/prek run placeholder-leftovers --all-files` runs
  `scripts/ci/check-placeholder-leftovers.sh`, which fails while any file outside `infra/adapt/excludes.txt` still names
  the placeholder.

---

## When Copier would be the better tool

Copier re-applies a template to a generated project and merges the differences from your answers, so the renames
never conflict. It would serve better when many adopters have drifted far enough that merges stop being practical,
when the template grows options that generate different file sets, or once Copier can update a project generated
from a plain, runnable repository. Until then the template stays a repository that runs and is tested as it is; the
decision and when to revisit it are in [ADR-0039](./adr/0039-copier-deferred.md).
