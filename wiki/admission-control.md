# Admission control: only signed, attested images

Two Kyverno policies in [`deploy/policies/`](../deploy/policies/) make the cluster refuse a pod whose
image from this repository is not signed and attested by the release build. They are examples to
adapt, not something the chart installs.

| Policy | Kind | Does |
|---|---|---|
| `kyverno-verify-images.yaml` | `ImageValidatingPolicy` | Verifies signature and provenance of `ghcr.io/<owner>/<repo>/*` |
| `kyverno-deny-image-aliases.yaml` | `ValidatingPolicy` | Denies alias spellings of that path, which the first policy would not match |

## Render before applying

The files hold placeholders that the service-name adapter does not touch. `<owner>/<repo>` is the
lowercase registry path; `<Owner>/<Repo>` is the repository exactly as GitHub spells it (the
certificate keeps that case). They are different spellings of one repository, and the repository
name need not equal the service name. Apply the rendered output, never the raw files:

```bash
scripts/render-admission-policy.sh <Owner>/<Repo> | kubectl apply -f -
```

The script rejects malformed input, escapes the regexp and fails if a placeholder is left.
Afterwards check that the gate is live: a server-side dry run of an unsigned image from the path must
be denied.

```bash
kubectl run unsigned-check --image=ghcr.io/<owner>/<repo>/backend:unsigned --dry-run=server
```

Admitted means the policy is not working; do not rely on it.

## What is enforced

For every image matching `ghcr.io/<owner>/<repo>/*`, written in the canonical form (lowercase,
no port):

- A keyless cosign signature whose certificate has all three of: the identity of this repository's
  `reusable-build.yml` workflow on `main` or a `v*` tag, the GitHub Actions OIDC issuer, and the
  workflow-repository claim equal to `<Owner>/<Repo>`. Identity plus issuer alone would also accept
  any other repository that calls the public reusable workflow. The values, and how to verify by
  hand, are in [Verifying an image](./security.md#verifying-an-image).
- A SLSA build provenance attestation (`https://slsa.dev/provenance/v1`) signed with that same
  identity.
- A signature entry in the public Rekor log.
- With `mutateDigest`, a tag is replaced by its digest at admission and the pod is admitted if the
  image verifies; an image that does not verify is denied. `verifyDigest` and `required` are on.
- The checks cover containers, init containers and ephemeral containers (`kubectl debug`).

The signature policy matches the image string literally. A spelling such as `ghcr.io:443/...` or
`GHCR.io/...` pulls the same artifact but is not matched, so the alias policy denies any container
image that names this path with a port or a different case. The two together cover this
repository's images on `ghcr.io`. Images from other registries are out of scope: add a
registry-allowlist policy (not shipped here) if every image must come from known registries.

Kyverno's image verification does not read a pod's `imagePullSecrets`. If the GHCR package is
private, give the policy registry credentials in its `spec.credentials`.

Attestations exist only for public repositories (or when `ATTESTATIONS_ENABLED` is set, see the
build workflow). In a private repository the provenance rule cannot be met; drop it knowingly
rather than leaving a policy that rejects every pod.

The policies use Kyverno's `ImageValidatingPolicy` and `ValidatingPolicy`, not the older
`ClusterPolicy` `verifyImages` rule. GitHub stores the provenance as a Sigstore bundle attached to
the image, and `ImageValidatingPolicy` is the Kyverno type that reads bundles in 1.20.

## Tests

`kyverno test deploy/policies --require-tests` runs offline in the `repo` CI job.

- The alias policy is plain CEL, so its suite is a full test: the canonical name and foreign images
  pass; a port, an uppercase host and a mixed-case path (also in init and ephemeral containers) are
  denied.
- The signature policy's suite proves only that images outside the glob pass untouched and that an
  image inside it is selected for verification. Checking a signature, the identity, the repository
  claim or the provenance needs a registry. Signed against unsigned images are tested live on a kind
  cluster in a later phase.
- `tests/supply_chain/test_admission_policy.py` pins the whole spec of both policies, and
  `tests/supply_chain/test_admission_render.py` the renderer. Changes under `deploy/` and `wiki/`
  run these tests on pull requests.

## Version pair

| Tool | Version | Source |
|---|---|---|
| cosign (signs and verifies in CI) | 3.1.4 | `tools/pins.tsv` |
| Kyverno CLI (runs the offline tests) | 1.20.0 | `tools/pins.tsv` |
| Kyverno in the cluster | 1.20.x | run the same minor as the CLI |

cosign 3 writes signatures and attestations in the Sigstore bundle format, so the signing and the
verifying side must be tested together. This pair is tested with cosign 3.1.4 and Kyverno 1.20.x;
other versions are untested here. When you bump cosign or Kyverno in `tools/pins.tsv`, bump the
numbers above in the same change; a test fails if they differ.

## GKE Binary Authorization

Kyverno is the recommended path for this template: it is the only option here that checks a keyless
certificate identity for images on `ghcr.io`. What Binary Authorization offers instead, from
Google's current documentation:

- **Continuous validation (CV)** monitors running Pods against platform policies and logs
  violations to Cloud Logging; "CV does not terminate running Pods"
  ([overview](https://docs.cloud.google.com/binary-authorization/docs/overview-cv)). It is in
  Preview, and its check sets are scoped to a namespace or service account.
- Its **Sigstore signature check** "supports only Artifact Registry repositories", so an image on
  `ghcr.io` cannot be checked. It is key-based: you create an ECDSA key pair (Cloud KMS or local) and
  put the public key in the policy; keyless signing is not described
  ([Sigstore check](https://docs.cloud.google.com/binary-authorization/docs/cv-sigstore-check)).
- Its **SLSA check** supports one trusted builder, Cloud Build, so GitHub provenance does not
  qualify ([SLSA check](https://docs.cloud.google.com/binary-authorization/docs/cv-slsa-check)).
- **Enforcement at deploy time** is the project-singleton policy: a default rule with
  `evaluationMode: REQUIRE_ATTESTATION`, `enforcementMode: ENFORCED_BLOCK_AND_AUDIT_LOG` and
  `requireAttestationsBy` an attestor
  ([policy reference](https://docs.cloud.google.com/binary-authorization/docs/policy-yaml-reference)).
  An attestor holds a public key
  ([creating attestors](https://docs.cloud.google.com/binary-authorization/docs/creating-attestors-cli)),
  so to use it with these images you would run a service that verifies the keyless signature and then
  signs an attestation with your key.

Run Binary Authorization as an extra platform gate only if you operate such an attestor.
