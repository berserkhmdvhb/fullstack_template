# ADR-0001: GitHub is the only forge and CI

## Status

Accepted.

## Date

2026-11-20

## Context

The template began on another forge, with its CI file, its package registry and its variables. Supporting two forges
doubles every workflow, every supply-chain control and every doc, and the second copy drifts. GitHub offers what the
template needs in one place: Actions on hosted runners, OIDC tokens for keyless signing and cloud login, artifact
attestations, a container registry (GHCR), environments with protection rules, rulesets and private vulnerability
reporting.

## Decision

The template targets GitHub only. CI is GitHub Actions on GitHub-hosted runners (no self-hosted runners). Images go to
`ghcr.io/<owner>/<repo>/<image>`, the path proven in CI; Google Artifact Registry (`*.pkg.dev`) is supported and
documented, not proven in CI. Nothing in the template refers to the old forge except deliberate history.

## Consequences

- Good: one pipeline to keep correct; the supply-chain controls (OIDC, attestations, environments) are native, not
  bolted on.
- Good: a fork or a new repository from the template runs green with no extra setup.
- Bad: an adopter on another forge must port the workflows themselves; nothing here helps.
- Bad: the registry path an adopter is most likely to use in production (Artifact Registry) is the one CI does not
  exercise.

## Alternatives considered

- Keep both forges: rejected, two pipelines would drift and double the review surface.
- A forge-neutral runner (a task runner the CI calls): the Makefile already holds the local commands, but the
  security controls (OIDC, environments, attestations) are forge-specific and cannot be abstracted.

## Links

- [CI/CD pipeline](../ci-cd.md), [`.github/workflows/`](../../.github/workflows/)
- [Platform setup: Artifact Registry](../platform-setup.md#optional-artifact-registry-only)
- [GitHub repository settings](../github-repository-settings.md)
