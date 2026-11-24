# ADR-0024: The chart is published and signed from its own workflow, at a repository-scoped path

## Status

Accepted.

## Date

2026-11-24

## Context

Adopters who install the chart from a registry need to verify it the way they verify images. Publishing it from the
image build workflow would let a failed chart push fail the image digests and the deploys, and an owner-wide chart
path would let two repositories of one owner overwrite each other's package.

## Decision

On a `v*` tag only, after every image is signed, `release.yml` calls
[`reusable-chart-release.yml`](../../.github/workflows/reusable-chart-release.yml). It packages the chart with
`version` and `appVersion` set to the tag (the `Chart.yaml` literals never ship), pushes it to
`oci://ghcr.io/<owner>/<repo>/charts/<chart>`, beside the images, then signs it keylessly and attests its provenance
by digest, with the chart workflow's own identity. The draft release gets `chart.env` (reference and digest). A push
to `main` publishes no chart, because deploys use the checked-out chart. Only `release-assets` waits for it.

## Consequences

- Good: a chart failure holds back only the release's assets, never the digests, deploys or VEX.
- Good: the chart's certificate names a workflow that only publishes charts, and its path cannot collide with another
  repository's.
- Bad: on the first release the package may need this repository given Write access in its GitHub settings, then a
  re-run.
- Bad: a second signing identity for verifiers to know.

## Alternatives considered

- A `chart` job inside `reusable-build.yml`: rejected, a failed chart push would fail the image workflow.
- `ghcr.io/<owner>/charts`: rejected, shared by every repository of the owner.
- A chart per `main` commit: rejected, it only fills the registry.

## Links

- [`.github/workflows/reusable-chart-release.yml`](../../.github/workflows/reusable-chart-release.yml)
- [Releasing: the chart](../releasing.md#the-chart), [Security: verifying the chart](../security.md#verifying-the-chart)
