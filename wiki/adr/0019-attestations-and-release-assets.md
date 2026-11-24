# ADR-0019: Attestations in the registry, SBOMs and VEX as release assets

## Status

Accepted.

## Date

2026-11-20

## Context

The original pipeline uploaded SBOMs to its forge's package registry, which GitHub does not have. Evidence about an
image (how it was built, what it contains, which CVEs do not apply) has to be findable from the image digest, by
humans and by admission control, and has to outlive the CI run.

## Decision

- The release build attaches a SLSA build-provenance attestation and CycloneDX SBOM attestations to each image by
  digest (`actions/attest-build-provenance`, `actions/attest-sbom`), pushed to the registry (`push-to-registry: true`)
  so admission control can read them; `gh attestation verify` checks them by hand.
- The keyless cosign signature and the provenance are what admission verifies
  ([ADR-0018](./0018-cosign-keyless-and-kyverno-admission.md)); the SBOM attestations are additional evidence.
- A `v*` release carries the SBOMs, the OpenVEX document, the licence report, the image and chart digests and a
  `provenance.md` as GitHub Release assets. VEX is a release asset, not an attestation.

## Consequences

- Good: evidence travels with the digest, in the same registry, and survives the run's retention.
- Good: a release's assets are immutable once published (immutable releases,
  [ADR-0022](./0022-repository-settings-as-a-script.md)).
- Bad: attestations exist only for public repositories (or with `ATTESTATIONS_ENABLED` in a private one).
- Bad: an OpenVEX attestation, which a verifier could check by identity, is a later item.

## Alternatives considered

- SBOMs as workflow artifacts only: rejected, they expire and are not tied to the digest.
- A second package registry for SBOMs: rejected, the registry beside the image already holds attestations.

## Links

- [`.github/workflows/reusable-build.yml`](../../.github/workflows/reusable-build.yml),
  [`.github/workflows/release.yml`](../../.github/workflows/release.yml)
- [Security: verifying an image](../security.md#verifying-an-image),
  [Vulnerability management: the release's OpenVEX](../vulnerability-management.md#the-releases-openvex)
