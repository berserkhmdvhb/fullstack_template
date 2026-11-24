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

The template's releases are its `v*` tags. Before its first tagged release, the record names the changelog's newest
version heading instead. A copy made from `main` between releases holds that release plus later commits: the
upgrade below then re-applies a few changes you already have, which merge cleanly or come out empty.

---

## Upgrading

1. **Compare the versions.** Read your `.template-version`, then the template's [Changelog](./changelog.md): every
   release after yours, and `[Unreleased]` if you take `main`.
2. **Read the adopter actions.** Each release's **Upgrading** list gathers the steps a running deployment needs (a
   bootstrap re-run, a settings re-run, a new value); every other entry says "Nothing to do when adopting" or links
   that list. Note the steps before you merge: some must be done before the next deploy.
3. **Bring the changes in** from a template remote, on a branch:

   ```bash
   git remote add upstream https://github.com/<template-owner>/<template-repo>.git
   git fetch upstream --tags
   git switch -c template-upgrade
   # A fork or clone shares the template's history: merge the release.
   git merge v<new>
   # A copy made with "Use this template" has no shared history: replay the template's commits between the releases
   # (each is a three-way merge against its own parent).
   git cherry-pick v<old>..v<new>
   ```

   For a few changes only, cherry-pick their commits from the changelog's links.
4. **Re-apply the adoption** to what came in: see [Conflicts](#conflicts-with-the-renamed-placeholders) below.
5. **Record the new version:** write `v<new>` to `.template-version` in the same pull request.
6. **Run the gates:** `make check-all-dry`, `make test` and `make precommit` locally, then the pull request's CI
   (`CI OK`). Do the Upgrading steps you noted, in the order the list gives.

---

## Conflicts with the renamed placeholders

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
- **Check that nothing is left:** `pre-commit run placeholder-leftovers --all-files` runs
  `scripts/ci/check-placeholder-leftovers.sh`, which fails while any file outside `infra/adapt/excludes.txt` still names
  the placeholder.

---

## When Copier would be the better tool

Copier re-applies a template to a generated project and merges the differences from your answers, so the renames
never conflict. It would serve better when many adopters have drifted far enough that merges stop being practical,
when the template grows options that generate different file sets, or once Copier can update a project generated
from a plain, runnable repository. Until then the template stays a repository that runs and is tested as it is; the
decision and when to revisit it are in [ADR-0039](./adr/0039-copier-deferred.md).
