# ADR-0016: Release images aim at SLSA Build L3

## Status

Accepted.

## Date

2026-11-20

## Context

A signature proves who signed, not how the artifact was built. SLSA's Build track grades that: L2 asks for signed
provenance from a hosted build; L3 adds a hardened build platform where one build cannot influence another and the
signing material is out of the build's reach. GitHub's documentation (the artifact attestations guide "Using
artifact attestations and reusable workflows to achieve SLSA v1 Build Level 3") describes reaching Build L3 when the
build runs in a reusable workflow and verifiers pin the signer workflow, so the provenance names the reusable
workflow rather than the calling one. Admission and `gh attestation verify --signer-workflow` do pin it.

## Decision

Release images (`main` and `v*` tags) are built, pushed, signed and attested only in the reusable workflow
[`reusable-build.yml`](../../.github/workflows/reusable-build.yml), on GitHub-hosted runners, with keyless signing
(the job's OIDC token, never a stored key). The hardening lint (`image-push-isolation`) proves that no other
workflow, job or composite action pushes, signs or attests an image or holds `packages` or `attestations` write; the
one other signing workflow is the chart's ([ADR-0024](./0024-signed-chart-from-its-own-workflow.md)).
The release path reads no build cache ([ADR-0017](./0017-release-builds-use-no-cache.md)). No pull request can
reach any of it ([`AGENTS.md`](../../AGENTS.md), rule 5).

## Consequences

- Good: the provenance's builder identity is one workflow file, which admission control pins
  ([ADR-0018](./0018-cosign-keyless-and-kyverno-admission.md)).
- Good: the lint fails closed when a future change adds a second image builder.
- Bad: the claim rests on GitHub's hosted-runner isolation, which the template cannot audit; it is the platform's
  claim, recorded as an assumption in the [threat model](../threat-model.md).
- Bad: builds are not reproducible bit for bit; that is a later item, not a Build L3 requirement.

## Alternatives considered

- Build L2 only (provenance from any workflow): rejected, a calling workflow anyone can edit would be the builder.
- The SLSA GitHub generator: rejected, GitHub's own attestations are native and pushed to the registry beside the
  image.

## Links

- [`.github/workflows/reusable-build.yml`](../../.github/workflows/reusable-build.yml),
  [`.github/workflows/release.yml`](../../.github/workflows/release.yml)
- [`infra/security/ci_hardening_lint.py`](../../infra/security/ci_hardening_lint.py)
- [Security: verifying an image](../security.md#verifying-an-image),
  [Supply-chain security](../supply-chain-security.md)
