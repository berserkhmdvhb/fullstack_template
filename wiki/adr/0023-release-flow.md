# ADR-0023: Releases are cut by release-please with a GitHub App token, as drafts with forced tags

## Status

Accepted.

## Date

2026-11-24

## Context

A release needs a version, a tag and a GitHub Release with its assets. The workflow's own `GITHUB_TOKEN` cannot do
it: a pull request or tag it creates triggers no workflows, so the release pull request would have no checks and the
tag would build nothing. Immutable releases reject assets added after publication, so the release must stay a draft
until the build has attached everything. The version must have one source: the tag.

## Decision

- release-please runs on every push to `main` ([`release-please.yml`](../../.github/workflows/release-please.yml)).
  Configured in [`release-please-config.json`](../../release-please-config.json), it opens the release
  pull request from Conventional Commit titles (squash merges with the checked pull-request title) and, on merge,
  creates a **draft** release with a **forced tag** (`force-tag-creation`), so the `v*` tag exists while the release
  is still a draft. The hand-written changelog is kept (`skip-changelog`).
- It runs with a GitHub App token (`actions/create-github-app-token`). The App ID is the repository variable
  `RELEASE_APP_ID`; its private key is a secret of the `release` environment. The App is the only actor allowed to
  create `v*` tags ([ADR-0022](./0022-repository-settings-as-a-script.md)).
- The tag triggers `release.yml`, which builds, signs, deploys and uploads the assets to that one draft. A human
  publishes it once the run is green.
- The tag is the version: `setuptools_scm` reads it, release-please's own version file is read by nothing, and `main`
  images stay `0.0.0-main-<sha>`.

## Consequences

- Good: the release pull request gets its checks, and the tag builds.
- Good: nothing is published before its assets are complete.
- Bad: the owner creates and installs a GitHub App once, and guards its key.
- Bad: a human step (publish) remains by design.

## Alternatives considered

- `GITHUB_TOKEN`: rejected, its tags and pull requests trigger no workflows.
- A personal access token: rejected, broad and tied to a person.
- Tag by hand with no release bot: still possible, but the version and notes drift from the commits.

## Links

- [`.github/workflows/release-please.yml`](../../.github/workflows/release-please.yml),
  [`release-please-config.json`](../../release-please-config.json), [`.github/workflows/release.yml`](../../.github/workflows/release.yml),
  [`.github/workflows/pr-title.yml`](../../.github/workflows/pr-title.yml)
- [Releasing](../releasing.md), [GitHub repository settings: the release GitHub App](../github-repository-settings.md#the-release-github-app)
- [Versioning](../versioning.md)
