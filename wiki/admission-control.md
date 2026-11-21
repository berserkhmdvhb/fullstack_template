# Admission control: only signed, attested images

The policy [`deploy/policies/kyverno-verify-images.yaml`](../deploy/policies/kyverno-verify-images.yaml)
makes the cluster refuse any pod whose image from this repository is not signed and attested by the
release build. It is an example to adapt, not something the chart installs.

## What is enforced

For every image matching `ghcr.io/<owner>/your-service-name/*` (the adapter rewrites the service
name; replace `<owner>` with the GitHub owner, lowercase):

- A keyless cosign signature whose certificate has all three of: the identity of this repository's
  `reusable-build.yml` workflow on `main` or a `v*` tag, the GitHub Actions OIDC issuer, and the
  workflow-repository claim equal to `<owner>/your-service-name`. Identity plus issuer alone would
  also accept any other repository that calls the public reusable workflow. The values, and how to
  verify by hand, are in [Verifying an image](./security.md#verifying-an-image).
- A SLSA build provenance attestation (`https://slsa.dev/provenance/v1`) signed with that same
  identity.
- A signature entry in the public Rekor log.
- The tag is replaced by the digest at admission (`mutateDigest`), and a pod that is not pinned
  to a digest is rejected (`verifyDigest`). Failure denies the pod (`Deny`, the Enforce equivalent).

Images outside the glob are not touched. Add a second policy (or widen the glob) for any other
registry you want covered.

The policy uses Kyverno's `ImageValidatingPolicy` and not the older `ClusterPolicy` `verifyImages`
rule. GitHub stores the provenance as a Sigstore bundle attached to the image, and Kyverno 1.20
verifies that bundle format through `ImageValidatingPolicy`.

Attestations exist only for public repositories (or when `ATTESTATIONS_ENABLED` is set, see the
build workflow). In a private repository the provenance rule cannot be met; drop it knowingly
rather than leaving a policy that rejects every pod.

## Tests

`kyverno test deploy/policies/tests` runs offline in the `repo` CI job. It proves that images
outside the glob pass untouched and that an image inside it is selected for verification. It cannot
check a signature, the identity, the repository claim or the provenance, because those need the
registry. The structure of the policy (exact regexp, issuer, repository claim, predicate type,
settings) is pinned by `tests/supply_chain/test_admission_policy.py`. Signed against unsigned images
are tested live on a kind cluster in a later phase.

## Version pair

| Tool | Version | Source |
|---|---|---|
| cosign (signs and verifies in CI) | 3.1.4 | `tools/pins.tsv` |
| Kyverno CLI (runs the offline tests) | 1.20.0 | `tools/pins.tsv` |
| Kyverno in the cluster | 1.20.x or newer | run the same minor as the CLI |

They must agree because cosign 3 writes signatures and attestations in the Sigstore bundle format,
and only Kyverno 1.20 and newer read that format with the identity and repository checks used here.
An older in-cluster Kyverno would see no verifiable signature and reject every image. When you bump
cosign or Kyverno in `tools/pins.tsv`, bump the numbers above in the same change; a test fails if
they differ.

## GKE Binary Authorization equivalent

Binary Authorization on GKE gates admission the same way. Use a platform policy (or the
continuous-validation policy for running pods) with a Sigstore signature check, and set the default
rule to deny. Its limits against this policy, as of writing (check Google's current documentation):

- Its Sigstore check matches a public key you register, not a keyless certificate identity, so it
  cannot express "this workflow in this repository". Keyless images need an attestor that you
  operate, or Kyverno.
- It does not inspect the SLSA provenance contents.
- It acts per project or cluster policy, not per namespace or image glob with a repository claim.

Run Kyverno for the identity checks and Binary Authorization as a second, platform-level gate.
