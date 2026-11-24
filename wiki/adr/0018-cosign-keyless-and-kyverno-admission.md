# ADR-0018: Admission verifies keyless cosign signatures and provenance with Kyverno

## Status

Accepted.

## Date

2026-11-20

## Context

Signing and attesting in CI proves nothing unless the cluster refuses what is not signed. The cluster needs one
verifier that understands keyless certificates (an OIDC identity, not a key) for images on `ghcr.io`, and the
Sigstore bundle format GitHub stores its attestations in.

## Decision

The template ships two example Kyverno policies, which the chart does not install:
- an `ImageValidatingPolicy` that admits an image under `ghcr.io/<owner>/<repo>/*` only with a keyless cosign
  signature whose certificate names this repository's `reusable-build.yml` on `main` or a `v*` tag, the GitHub Actions
  issuer and the repository claim, plus a SLSA provenance attestation signed by the same identity, logged in Rekor;
  it replaces a tag by its digest at admission;
- a `ValidatingPolicy` that denies alias spellings of that path (a port or a different case).

cosign and Kyverno are pinned to a pair that agree on the cosign v3 bundle format (the
[version pair](../admission-control.md#version-pair), pinned in [`tools/pins.tsv`](../../tools/pins.tsv)).
Kyverno is the one admission path; the Sigstore policy-controller is not shipped.

## Consequences

- Good: an unsigned, unattested or other-identity image from this path is refused, proven on kind.
- Good: the policy is rendered from placeholders by a script that refuses a leftover placeholder.
- Bad: it is an example to apply, not enforced by default; until an admin applies it, nothing is refused.
- Bad: images from other registries are out of scope; a registry allowlist is the adopter's to add.
- Bad: a private repository gets no attestations, so the provenance rule must be dropped knowingly.

## Alternatives considered

- Sigstore policy-controller beside Kyverno: cut, two verifiers to keep in step for one check.
- GKE Binary Authorization: its Sigstore check is key-based and Artifact Registry only, and its SLSA check trusts
  Cloud Build only, so it cannot check these images.

## Links

- [`deploy/policies/kyverno-verify-images.yaml`](../../deploy/policies/kyverno-verify-images.yaml),
  [`deploy/policies/kyverno-deny-image-aliases.yaml`](../../deploy/policies/kyverno-deny-image-aliases.yaml),
  [`scripts/render-admission-policy.sh`](../../scripts/render-admission-policy.sh)
- [Admission control](../admission-control.md)
- [`tests/k8s/test_admission.py`](../../tests/k8s/test_admission.py)
