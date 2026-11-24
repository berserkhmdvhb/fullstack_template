# ADR-0020: Every environment deploys by digest

## Status

Accepted.

## Date

2026-11-20

## Context

A tag is a mutable pointer: the image behind `backend:1.2.3` can change after it was tested, signed or approved.
What was verified must be exactly what runs, in every environment, including the one a reviewer approves.

## Decision

The release workflow resolves each image's digest once, verifies it, and passes the digests to every deploy. The
deploy script sets each image by `sha256:<64 hex>` (and refuses any other form), and the post-deploy check compares
the running digest with the expected one. With `environment: production` the chart refuses to render an image
without a digest. Admission replaces any tag it sees by its digest. Previews read the digests dev runs.

## Consequences

- Good: what was scanned, signed and approved is what runs; a rollback goes back to a known digest.
- Good: a re-pushed tag cannot change a running environment.
- Bad: values files hold placeholder digests that the deploy fills; a hand deploy must pass the digests too.

## Alternatives considered

- Deploy by immutable tag: rejected, tag immutability is a registry setting that can change, and verification
  works on digests.

## Links

- [`scripts/ci/deploy-env.sh`](../../scripts/ci/deploy-env.sh), [`scripts/ci/deploy-verify.sh`](../../scripts/ci/deploy-verify.sh),
  [`infra/security/verify-image-digest.sh`](../../infra/security/verify-image-digest.sh)
- [CI/CD: deploy chain](../ci-cd.md#deploy-chain), [Containerization: immutable digest-pin](../containerization.md#immutable-digest-pin)
