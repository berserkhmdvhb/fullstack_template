# Tooling assessment: everything mise does not manage

> Date: 2026-11-25. Scope: every layer outside the developer toolchain that
> [ADR-0043](./adr/0043-mise-for-the-developer-toolchain.md) gives to mise: the application's packages, the cluster
> and deploy configuration, secrets, the task runner, the git hooks, dependency updates, the container base images and
> the scanners' own installer. This page changes no tool; it records what the template uses, what the mature
> alternatives are today, and a verdict per layer.
>
> **Verdict:** the template is at current practice on most layers. Three gaps are worth closing now, none of which
> changes how an adopter installs, builds or deploys: the frontend's lockfile has no release-age floor (with one, `npm ci`
> still installs the lock as it is, tried on the assessment date; only adding a package younger than seven days needs
> the existing exemption flag), the distroless base images' signatures are never verified, and the secrets docs
> contradict each other (with one dead install URL). Three swaps are the
> maintainers' decision: pnpm for npm, External Secrets with Secret Manager as the production default, and prek for
> the git hooks. The recommendation was to take the second and hold the other two; the maintainers chose prek too
> ([ADR-0044](./adr/0044-prek-for-git-hooks.md)).

## 0. How each layer is judged

"More mature" means widely adopted, maintained, secure by default, and fitting this template's constraints:
hash-pinned, checksum-verified, offline tests, no secrets in Git, read-only pull request jobs, least privilege. Newer
is not enough.

Each layer gets one of three verdicts:

- **keep**: the template already follows current practice, and the reason is given;
- **adopt now**: a scoped change that closes a gap without changing how an adopter works;
- **maintainers' decision**: a swap of a core tool (a package manager, the task runner, the hook runner) or anything
  that changes how every adopter works. It comes with a recommendation and its cost.

Every version and date below was read from the registry or the release page on 2026-11-25; the sources are listed in
[section 12](#12-sources).

## 1. Python packages: uv and `uv.lock`

**Today.** uv (pinned in [`mise.toml`](../mise.toml)) resolves [`pyproject.toml`](../pyproject.toml) into
[`uv.lock`](../uv.lock), with a sha256 for every artifact. `[tool.uv] exclude-newer = "7 days"` keeps every locked
release at least seven days old, the PEP 517 build backend is pinned with hashes in `build-constraint-dependencies`,
and [`Dockerfile.backend`](../Dockerfile.backend) installs with `uv sync --frozen --no-dev`, which checks every
download against the lock. The `uv-lock` hook runs `uv lock --check --offline`. The rationale is
[ADR-0015](./adr/0015-uv-for-python.md).

**Alternatives.** Poetry 2.5.2 (2026-11-21) and PDM 2.29.7 (2026-11-06) are maintained, lock with hashes, and Poetry
now has a release-age setting (`solver.min-release-age`). uv itself releases often (0.13.10 on 2026-11-24); its pin
is in `mise.toml` and moves with Renovate under the seven-day rule.

**Verdict: keep.** uv already does everything the alternatives do here, and more that the template uses: a relative
release-age span stored in the lock (`exclude-newer`, "Accepts ... a 'friendly' duration (e.g., 24 hours, 1 week,
30 days)"), hashed build dependencies, and a frozen install in the image. Nothing would get stronger by switching.

**Watch (the maintainers' call).** uv's settings reference now has a `[tool.uv.audit]` table, with an optional
`malware-check` during sync against `https://api.osv.dev/` (default `false`). It stays off: it would make every
`uv sync` call the network (the tests and the image build are offline from the lock), and the malicious-package check
already runs in CI (OSV-Scanner, [Supply-chain tooling assessment](./supply-chain-tooling-assessment.md)). What would
reopen it: OSV-Scanner stops covering PyPI's `MAL-*` entries, or uv's audit can replace pip-audit (which the
supply-chain assessment keeps because OSV-Scanner does not read `pyproject.toml`). Either is a scanner change, made
the way that page describes.

## 2. The frontend's and the docs site's packages: npm and `package-lock.json`

**Today.**

- npm, pinned as `packageManager` in [`frontend/package.json`](../frontend/package.json) and
  [`docs-site/package.json`](../docs-site/package.json). On the assessment date it is the npm that the pinned Node
  ([`mise.toml`](../mise.toml), an LTS release) bundles, per nodejs.org's release index.
- Both projects lock with `lockfileVersion: 3`. Every `resolved` URL in both lockfiles points at
  `registry.npmjs.org`: no git and no tarball-URL dependencies.
- Every install is `npm ci --ignore-scripts`: CI, the kind tests, both image builds and the documented local install.
  `allowScripts` denies the packages that declare an install script, and
  `tests/supply_chain/test_npm_install_scripts.py` fails when the frontend's lockfile gains one without an entry
  ([Install scripts](./frontend-deployment.md#install-scripts)).
- The release age: [`docs-site/.npmrc`](../docs-site/.npmrc) sets `min-release-age=7`. **The frontend has no
  `.npmrc`**, so nothing holds its lockfile to seven days. Renovate waits seven days for the package it updates, but
  the `npm install` that writes the lock resolves that package's transitive dependencies at their newest. A
  developer's `npm install <package>` does the same.

**What npm offers now** (npm config reference, v11):

- `min-release-age` (added in npm 11.10.0): "If set, npm will build the npm tree such that only versions that were
  available more than the given number of days ago will be installed. If there are no versions available for the
  current set of dependencies, the command will error." `min-release-age-exclude` came in 11.17.0.
- `allow-git` (added in 11.9.0) and `allow-remote` (added in 11.14.0, per npm's changelog): they limit git and
  tarball-URL dependencies. Both default to `"all"` in npm 11. An older npm warns on them as unknown keys; the pinned
  npm knows both (`npm config ls -l` lists them, and an install with them set prints no warning).
- `allowScripts` in `package.json`: the per-project install-script policy.

npm 12.0.0 (2026-07-08) goes further. Its release notes: "Dependency lifecycle scripts are now blocked by default
unless allowed by the root package's allowScripts policy", and "allow-git and allow-remote now default to 'none'".
No Node LTS bundles npm 12 yet; the 11.x line still gets releases (11.21.3, 2026-11-18).

**Alternatives.**

| Manager | Version (date) | Install scripts | Release age | Other controls |
|---|---|---|---|---|
| pnpm | 12.23.0 (2026-11-22) | `strictDepBuilds` default `true`: an install fails on any unreviewed build script; `allowBuilds` lists the decisions | `minimumReleaseAge`, default 1440 minutes (one day), transitive included | `blockExoticSubdeps` default `true` (only direct dependencies may come from git or a URL); `trustPolicy: no-downgrade` (opt-in) fails when a package's trust evidence drops, for example from trusted publishing to none |
| Yarn (Berry) | 4.19.0 (2026-11-21) | `enableScripts` default `false` | `npmMinimalAgeGate` | (not assessed further) |
| Bun | 1.4.5 (2026-11-24) | `trustedDependencies` | `minimumReleaseAge`, default disabled | a different runtime as well as a manager |

npm's config reference has no counterpart to pnpm's `trustPolicy`.

**Verdict: keep npm; adopt now** the two settings that close the gap with npm alone, in
a new `.npmrc` in `frontend/`, and in `docs-site/.npmrc`:

```ini
min-release-age=7
allow-git=none
allow-remote=none
```

This is the docs site's rule, extended to the frontend, plus npm 12's defaults brought forward. It holds the
frontend's transitive dependencies to the same seven days as everything else, and blocks the exotic sources pnpm
blocks by default.

**What the setting does to each install** (tried on 2026-11-25 with the pinned npm, in a scratch project whose lock
held a dependency published less than five days earlier, `undici-types` 8.13.1, and an `.npmrc` with the three lines above):

| Command | Result |
|---|---|
| `npm ci --ignore-scripts` | installs the locked version, exit 0: `npm ci` follows the lock and does not apply the window |
| `npm install --ignore-scripts` with the lock in place | keeps the locked version |
| `npm install undici-types@8.13.1 --ignore-scripts` | refused, exit 1: `ETARGET`, "No matching version found for undici-types@8.13.1 with a date before ..." |
| the same with `--min-release-age-exclude=undici-types` | installs it, exit 0 |
| `npm install undici-types@latest --ignore-scripts` | picks the newest version older than seven days (8.12.4) and rewrites the lock to it |

So CI, the image builds and the documented local install (`npm ci`) do not change. What changes is the command that
writes the lock: adding or bumping a package younger than seven days now needs the existing young-fix step
(`--min-release-age-exclude=<package>`, [Security updates younger than 7
days](./getting-started.md#security-updates-younger-than-7-days)), which then has to name the frontend as well as the
docs site. Neither lockfile has a git or URL dependency, so `allow-git=none` and `allow-remote=none` change no install.

**pnpm is the maintainers' decision** (section 11). The template already has the install-script control pnpm makes
default (`--ignore-scripts` everywhere plus `allowScripts`). With the settings above, it also has the release age and
the exotic-source block. What pnpm would add is `trustPolicy: no-downgrade`, its content-addressed store, and strict
failures on any new build script.

## 3. The cluster configuration: Helm values and `values.schema.json`

**Today.** One chart, [`charts/your-service-name`](../charts/your-service-name), with one values file per environment
(`values.dev.yaml`, `values.qa.yaml`, `values.production.yaml`, `values.preview.yaml`, `values.local.yaml`) over
`values.yaml`. [`values.schema.json`](../charts/your-service-name/values.schema.json) (JSON Schema draft-07, closed objects) makes Helm refuse an unknown key or a wrong type on every `lint`, `template` and
`install`. `templates/validate-values.yaml` refuses invalid combinations and placeholders, and CI runs `ct lint`,
`helm lint`, helm-unittest, and kubeconform (strict, offline) over every environment's render
([`reusable-chart.yml`](../.github/workflows/reusable-chart.yml)).

**Alternatives.**

- Kustomize v5.8.3 (2026-10-08): overlays over plain manifests, with no schema of its own and no install lifecycle
  (hooks, rollback, release history).
- Helmfile v1.8.5 (2026-11-22): "a declarative spec for deploying helm charts". It sits on top of Helm and helps when
  one repository deploys many releases; this template deploys one chart per environment from one workflow.
- Timoni v0.38.0 (2026-11-25) on CUE v0.18.0 (2026-11-24), and KCL v0.13.3 (2026-11-17): typed configuration
  languages. All are still below 1.0.

**Verdict: keep.** A schema-validated chart with per-environment values, linted and rendered strictly in CI, is the
mature practice for one service. The typed alternatives are pre-1.0, and Kustomize would drop the schema and the
release lifecycle. A switch would rewrite the chart, the deploy workflow, the bootstrap and every chart test.

## 4. Secrets

**Decided: External Secrets with Google Secret Manager is the default for dev, qa and production.** The app Secret
has exactly one of three sources, and two at once fail the render ([Secret sources](./security.md#secret-sources)):

1. `secrets.externalSecret.enabled`: an `external-secrets.io/v1` `ExternalSecret`
   ([`templates/externalsecret.yaml`](../charts/your-service-name/templates/externalsecret.yaml)) per namespace,
   synced from the environment's store: dev, qa and production. Each namespace has its own `SecretStore` and
   identity, which reads only its environment's secrets
   ([GKE Cluster Setup, External Secrets](./gke-cluster-setup.md#14-the-apps-secrets-external-secrets-with-secret-manager));
2. `secrets.existingSecret`, a Secret managed outside the chart: previews, whose bootstrap makes it;
3. the chart's own Secret from `secrets.dbPassword` and `secrets.statsPass`, fed from a gitignored
   `values.secret.yaml`: local and kind.

The store reference and a remote-key template are values per environment, so moving to another store changes no
template and no app code; the kind job runs the chart against OpenBao with only those values switched. The deploy
jobs hold no app secret: GitHub holds only the deploy credentials, in environment-scoped secrets.

**What the docs said, and what changed.** [`security.md`](./security.md#secret-sources) headed a section "Sealed
Secrets (Recommended)" and installed the controller from `https://bitnami-labs.github.io/sealed-secrets`, which now
returns 404; it is now one way to fill `existingSecret`, from `https://bitnami.github.io/sealed-secrets` (200), the
project's current README. [`disaster-recovery.md`](./disaster-recovery.md) states that the credentials "live in Secret
Manager, and the External Secrets Operator syncs them again into the restored namespace", which held only with
source 1 and is now the default. The GKE guide installs the operator and the stores.

**Alternatives.**

| Option | Version (date) | How the app gets the value | Fit |
|---|---|---|---|
| External Secrets Operator + Secret Manager | chart 2.15.0 (2026-11-10) | a synced Kubernetes Secret, read as env vars as today | the chart already renders it; portable across clouds; a CNCF project (its security contact is a `lists.cncf.io` address). **The default** |
| External Secrets Operator + OpenBao | OpenBao v2.7.2 (2026-10-26) | the same synced Secret, from OpenBao's KV engine | MPL-2.0, a fork of Vault under the Linux Foundation; runs anywhere, no cloud account. You run it: storage, high availability, unsealing, upgrades and its own backups. Least privilege per environment through Kubernetes-auth roles and path policies. **Documented** ([OpenBao](./security.md#openbao)) |
| External Secrets Operator + Infisical | v0.167.x (weekly releases) | the same synced Secret, from an Infisical project | MIT outside an `ee/` directory under its own licence; a hosted service or self-hosted. Per-environment machine identities, but through the operator the namespace-scoped login is Universal Auth, a client secret in each namespace. **Documented** ([Infisical](./security.md#infisical)) |
| Secrets Store CSI driver + GCP provider, or GKE's Secret Manager add-on | driver v1.6.2 (2026-11-05), provider v1.20.0 (2026-11-20) | files on a mounted volume | the app reads env vars, so it would need code changes. Google's docs: "The Secret Manager add-on doesn't support the Sync as Kubernetes Secret feature"; GKE's separate sync uses a GKE-only `SecretSync` resource |
| SOPS | v3.13.3 (2026-07-23) | encrypted values files in Git, decrypted at deploy | ciphertext in Git, and the deploy job needs a KMS decrypt grant |
| Sealed Secrets | v0.41.2 (2026-10-26) | a `SealedSecret` in Git, decrypted by a per-cluster controller | one controller key per cluster to back up and rotate; no link to a cloud store. One way to fill `existingSecret` |

**Cost of the decision**, for an adopter: before the first deploy of each environment, install the operator, the
Secret Accessor bindings and a store per namespace, create the secrets in Secret Manager, and name the store in
`values.<env>.yaml` (the placeholder makes the deploy refuse until then). One more platform component to run and
upgrade (its chart, image and CRDs move together in one Renovate group). `existingSecret` stays for a cluster without
the operator.

## 5. Deploy settings: GitHub environments and variables

**Today.** [`deploy.yml`](../.github/workflows/deploy.yml) deploys through the `dev`, `qa` and `production`
environments:

- Deploy rules: `dev` and `qa` take branch `main` and tag `v*`; `production` takes tag `v*` only, with a required
  reviewer.
- The cloud login is Workload Identity Federation, with one deployer service account per environment and
  `id-token: write` only on the deploy jobs.
- Every non-secret setting is a variable: one table in [Platform Setup](./platform-setup.md#variables-and-secrets),
  kept equal to the settings script by a test. The one optional secret, `KUBECONFIG_B64`, is environment-scoped.
- [`scripts/github-settings.sh`](../scripts/github-settings.sh) applies the environments, their rules and the
  variables as code ([GitHub repository settings](./github-repository-settings.md)).

**Practice.** GitHub's environment docs give required reviewers, deployment branch and tag rules, and environment
secrets. Its OIDC docs recommend short-lived cloud tokens over "long-lived GitHub secrets". The template uses all
four.

**Verdict: keep.** Per-environment deploy rules and OIDC with no stored cloud key is current practice. Helm values
hold what the cluster runs; GitHub holds who may deploy where.

## 6. The task runner: make

**Today.** The [`Makefile`](../Makefile) is the front door: the docs, the workflows and the hooks call its targets.
Measured on the assessment date, 145 tracked files name a `make` target, 8 of them under `.github/`; that is the size
of a switch.

**Alternatives.**

- just 1.59.0 (2026-10-15), "just a command runner".
- Task v3.54.0 (2026-10-01), YAML Taskfiles.
- mise tasks, ruled out by ADR-0043: `mise.toml` holds no tasks, so mise stays the toolchain and nothing else.

**Verdict: keep.** make is already on every CI runner and in the devcontainer, so it needs no pin, and the targets
are plain shell calls into scripts that CI runs too. A switch would rewrite those files and every adopter's
habits for no gain in security or reproducibility. A maintainer who wants nicer syntax should weigh it against that
cost. It is not recommended.

## 7. Git hooks: prek

**Today.** prek 0.5.11 (released 2026-11-16), a single binary that mise locks from [`mise.toml`](../mise.toml) with a
sha256 per platform and a GitHub attestation ([ADR-0044](./adr/0044-prek-for-git-hooks.md)). It reads
[`.pre-commit-config.yaml`](../.pre-commit-config.yaml), where every hook is `repo: local` with `language: system`:
each calls the tool CI uses, nothing is fetched, and nothing needs the network. Before the switch the runner was
pre-commit (first released 2014), installed into its own venv from hash-pinned requirements.

**Alternatives.**

- pre-commit, the previous runner, first released 2014: the reference implementation, Python.
- hk v2.14.0 (2026-11-23), configured in Pkl.
- lefthook v2.2.3 (2026-11-24), YAML. Both would need a new config file.

**Verdict: prek (the maintainers' decision, 2026-11-25).** Its README: "Fully compatible with the original pre-commit
configurations and hooks", "A single binary with no dependencies, does not require Python". The aqua registry has a
recipe with a checksum file and an attestation, so mise locks and verifies it like the other tools. The gains prek is
built for (speed and disk when hooks build their own environments) barely apply to local system hooks; what decided it
is one verified binary and no Python venv for the runner. prek is not yet 1.0: a bump is checked by running the hooks
on a clean and a broken tree.

## 8. Dependency updates: Renovate (Dependabot for security only)

**Today.** [`renovate.json5`](../renovate.json5):

- a 7-day `minimumReleaseAge`, a weekly minor and patch group, and majors on their own;
- digest pins for Actions and images;
- the `mise` manager, which runs `mise lock` in safe mode (`MISE_SAFE=1`, per Renovate's mise manager docs), plus
  regex managers for the lines it cannot read.

[`.github/dependabot.yml`](../.github/dependabot.yml) is limited to security updates (`open-pull-requests-limit: 0`),
with a 7-day cooldown.

**Alternatives.** Renovate is at 44.262.2 (npm, 2026-11-25). Dependabot's `package-ecosystem` list (its supported
ecosystems page and the `dependabot.yml` options reference, both rechecked on 2026-11-25) has no mise entry, and the
options reference has no custom or regex manager, so it cannot update `mise.toml`, `tools/pins.tsv`, `infra/security/config.yaml` or
the digests in values files.

**Verdict: keep.** Renovate is the only one of the two that covers every pinned file here, and the split (Renovate for
versions, Dependabot for GitHub's security alerts) is already in place.

## 9. Container base images

**Today.**

- Each Dockerfile declares its runtime base once, by digest:
  - `gcr.io/distroless/python3-debian13` ([`Dockerfile.backend`](../Dockerfile.backend));
  - `gcr.io/distroless/nodejs26-debian13` ([`Dockerfile.frontend`](../Dockerfile.frontend));
  - Envoy's distroless image, by tag and index digest ([`Dockerfile.waf`](../Dockerfile.waf)).
- The build stages use `debian:13-slim`, the Node image and the uv image, all by digest (the tags are in the
  Dockerfiles).
- Renovate's `docker:pinDigests` keeps the digests current; Envoy's tag has its own versioning rule.
- A `debian-fixes` stage lays a Debian security fix over a base that has not shipped it yet
  ([Debian fixes](./containerization.md#debian-fixes-the-base-has-not-shipped-yet)).
- The images are scanned at build and weekly ([`rescan.yml`](../.github/workflows/rescan.yml)).

**Alternatives.**

- Chainguard's free images are "limited to the latest build of a given image, tagged as latest and latest-dev";
  version tags and patch SLAs are paid.
- Docker Hardened Images: "DHI's core features are free to use, share, and build on under Apache 2.0", but the
  community images come from `dhi.io` after `docker login dhi.io` with a Docker account. A registry login in a pull
  request build breaks [convention 5](../CONVENTIONS.md#5-image-level-ops-run-on-main-and-release-tags-only).

**The gap.** The distroless README: "All distroless images are signed by cosign with ephemeral keys (keyless). We
recommend verifying any distroless image you use before building your image". It gives the identity
`keyless@distroless.iam.gserviceaccount.com` and the issuer `https://accounts.google.com`. The template never checks
it: no file names that identity. `cosign tree` (2026-11-25) shows a signature and an attestation for both pinned
distroless digests. It shows none for the pinned Envoy, `debian:13-slim` and `node` digests, so those three cannot be
checked this way.

**Verdict: keep the bases; adopt now** (done: [Base image signatures](./containerization.md#base-image-signatures); `node` has none either) a `cosign verify` of the two distroless digests with that identity and issuer,
before the build. It reads the refs from the Dockerfiles, uses the pinned cosign, and needs no secret and no write
token. A changed digest that Google did not sign then fails before anything is built on it.

## 10. The scanners' own installer (frozen)

[`infra/security/tools-install.sh`](../infra/security/tools-install.sh) and
[`infra/security/config.yaml`](../infra/security/config.yaml) install and pin the scanners (Syft, Grype, OSV-Scanner
and the rest) with their own checksums. Both are frozen ([AGENTS.md](../AGENTS.md) rule 6), and ADR-0043 leaves them
out of mise. Noted only; not assessed.

## 11. Summary

| Layer | Today | Verdict | Reason |
|---|---|---|---|
| Python packages | uv, `uv.lock` (hashes, `exclude-newer` 7 days, hashed build backend) | keep | Already the strongest option: frozen hashed installs and a release-age span in the lock |
| Frontend and docs-site packages | npm, `package-lock.json`, `--ignore-scripts`, `allowScripts` | keep; adopt now: `.npmrc` release age and exotic-source block; pnpm: maintainers' decision | The frontend lock has no release-age floor; npm can close it without a switch |
| Cluster configuration | Helm values per environment, `values.schema.json`, strict offline renders | keep | Schema-validated chart linted per environment; the typed alternatives are pre-1.0 |
| Secrets | three exclusive sources; External Secrets with Secret Manager in dev, qa and production | decided: External Secrets the default, OpenBao and Infisical documented (done); the docs agree, and the Sealed Secrets URL is the current one (done) | The shape was right; the docs disagreed and one install URL was dead |
| Deploy settings | GitHub environments, variables, OIDC Workload Identity | keep | Per-environment rules and no stored cloud key |
| Task runner | make | keep | Preinstalled, plain, and a switch would touch every file that names a target, for no security gain |
| Git hooks | prek, local system hooks | prek (maintainers' decision, [ADR-0044](./adr/0044-prek-for-git-hooks.md)) | One locked, attested binary and no Python venv; prek is pre-1.0 |
| Dependency updates | Renovate; Dependabot for security only | keep | The only updater that covers mise, the pin files and the values digests |
| Base images | distroless and Debian by digest, Renovate, `debian-fixes` | keep; verify the distroless signatures (done) | The vendor signs them and asks users to verify; the template does not |
| Scanners' installer | `infra/security/tools-install.sh` | frozen; noted | Out of scope |

**Adopt now** (each a scoped change that leaves how an adopter installs, builds and deploys as it is):

1. **The frontend's release age and the exotic-source block.** Add an `.npmrc` to `frontend/` with `min-release-age=7`,
   `allow-git=none` and `allow-remote=none`, and add the two `allow-*` lines to `docs-site/.npmrc`. Then:
   - extend the docs site's `.npmrc` test to both files;
   - make the young-fix step in [getting-started](./getting-started.md#security-updates-younger-than-7-days) and the
     comment in `.github/dependabot.yml` cover the frontend: adding or bumping a frontend package younger than seven
     days then needs `--min-release-age-exclude=<package>`.

   `npm ci` installs the lock as it is (section 2), so CI, the images and the local install do not change; a run of
   the frontend jobs on the change confirms it on the real lock.
2. **Verify the distroless base signatures.** Add a script that takes every `gcr.io/distroless/...@sha256:` reference
   from the Dockerfiles and runs the pinned cosign with the vendor's identity and issuer. Call it in the image check
   job before the build. Add a test that the script finds every distroless reference and fails on a wrong identity,
   and a line in [Containerization](./containerization.md).
3. **Make the secrets docs agree: done.** `security.md` heads with External Secrets; Sealed Secrets is one way to fill
   `existingSecret`, from `https://bitnami.github.io/sealed-secrets`. With External Secrets the default (decision 1),
   `disaster-recovery.md` is true as written.

**Maintainers' decision:**

1. **External Secrets with Secret Manager as the default: decided, done** ([Secrets](#4-secrets)). dev, qa and
   production turn `secrets.externalSecret.enabled` on, with a namespaced `SecretStore` per namespace whose Workload
   Identity principal reads only its environment's secrets (an IAM condition on the `<service>-<env>-` prefix), not a
   `ClusterSecretStore` with one identity for every environment. The GKE guide installs the pinned operator and the
   stores; the store reference and key template are values, so OpenBao and Infisical are documented alternatives a
   values change away, and the kind job proves the switch against OpenBao. `existingSecret` stays for previews and
   for clusters without the operator.
2. **pnpm instead of npm.** Not recommended now. With adopt-now item 1, npm has the release age, the install-script
   policy and the exotic-source block. pnpm would add `trustPolicy: no-downgrade` and strict build failures. Cost:
   - two lockfiles regenerated (`pnpm-lock.yaml`), a pnpm pin in `mise.toml`, and `packageManager` in both projects;
   - [`Dockerfile.frontend`](../Dockerfile.frontend), the `setup-frontend` action and five workflows;
   - the install-scripts guard and its test rewritten for `allowBuilds`;
   - every tracked file that names an npm command or the lockfile (measured on the assessment date: 63, 18 of them
     wiki pages).

   Revisit when a Node LTS bundles npm 12 (scripts blocked by default) or if npm stays without a trust-downgrade check.
3. **prek instead of the previous runner: done** ([ADR-0044](./adr/0044-prek-for-git-hooks.md)). The row and its requirements
   moved to a `mise.toml` entry, the `precommit` target runs prek, and the docs say `prek install`;
   `.pre-commit-config.yaml` stays as it is.

## 12. Sources

All checked on 2026-11-25.

- uv: [PyPI](https://pypi.org/project/uv/) (0.13.10, 2026-11-24); [settings
  reference](https://docs.astral.sh/uv/reference/settings/) (`exclude-newer`, `build-constraint-dependencies`,
  `[tool.uv.audit] malware-check`).
- Poetry: [PyPI](https://pypi.org/project/poetry/) (2.5.2, 2026-11-21); [configuration](https://python-poetry.org/docs/configuration/)
  (`solver.min-release-age`). PDM: [PyPI](https://pypi.org/project/pdm/) (2.29.7, 2026-11-06).
- npm: registry (`npm view npm time`: 12.0.0 2026-07-08, 11.21.3 2026-11-18); [config
  reference v11](https://docs.npmjs.com/cli/v11/using-npm/config) (`min-release-age`, `allow-git`, `allow-remote`,
  `allow-scripts`); [v12.0.0 release notes](https://github.com/npm/cli/releases/tag/v12.0.0); [Node.js release
  index](https://nodejs.org/dist/index.json) (the npm each release bundles; the newest npm in any LTS is an 11.x);
  [npm 11 changelog](https://github.com/npm/cli/blob/release/v11/CHANGELOG.md) (`allow-git` in 11.9.0,
  `min-release-age` in 11.10.0, `allow-remote` in 11.14.0, `min-release-age-exclude` in 11.17.0); the `npm ci` trial in section 2, with the
  pinned npm and `undici-types` 8.13.1 (published 2026-11-20).
- pnpm: registry (12.23.0, 2026-11-22); [build settings](https://pnpm.io/settings/build) (`strictDepBuilds`,
  `allowBuilds`); [dependency resolution settings](https://pnpm.io/settings/dependency-resolution)
  (`minimumReleaseAge`, `blockExoticSubdeps`, `trustPolicy`).
- Yarn: registry (`@yarnpkg/cli-dist` 4.19.0, 2026-11-21); [yarnrc](https://yarnpkg.com/configuration/yarnrc)
  (`enableScripts`, `npmMinimalAgeGate`). Bun: registry (1.4.5); [bunfig](https://bun.com/docs/runtime/bunfig)
  (`minimumReleaseAge`).
- Kustomize [v5.8.3](https://github.com/kubernetes-sigs/kustomize/releases/tag/kustomize/v5.8.3); Helmfile
  [v1.8.5](https://github.com/helmfile/helmfile/releases/tag/v1.8.5) and its [docs](https://helmfile.readthedocs.io/en/latest/);
  Timoni [v0.38.0](https://github.com/stefanprodan/timoni/releases/tag/v0.38.0); CUE
  [v0.18.0](https://github.com/cue-lang/cue/releases/tag/v0.18.0); KCL [v0.13.3](https://github.com/kcl-lang/kcl/releases/tag/v0.13.3).
- External Secrets [chart 2.15.0](https://github.com/external-secrets/external-secrets/releases/tag/helm-chart-2.15.0)
  and [README](https://github.com/external-secrets/external-secrets); Secrets Store CSI driver
  [v1.6.2](https://github.com/kubernetes-sigs/secrets-store-csi-driver/releases/tag/v1.6.2); GCP provider
  [v1.20.0](https://github.com/GoogleCloudPlatform/secrets-store-csi-driver-provider-gcp/releases/tag/v1.20.0); [GKE
  Secret Manager add-on](https://cloud.google.com/secret-manager/docs/secret-manager-managed-csi-component) and
  [sync to Kubernetes Secrets](https://cloud.google.com/secret-manager/docs/sync-k8-secrets); SOPS
  [v3.13.3](https://github.com/getsops/sops/releases/tag/v3.13.3); Sealed Secrets
  [v0.41.2](https://github.com/bitnami/sealed-secrets/releases/tag/v0.41.2) and its
  [README](https://github.com/bitnami/sealed-secrets) (the Helm repository `https://bitnami.github.io/sealed-secrets`
  answers 200; `https://bitnami-labs.github.io/sealed-secrets/` answers 404); OpenBao
  [v2.7.2](https://github.com/openbao/openbao/releases/tag/v2.7.2) and its MPL-2.0
  [licence](https://github.com/openbao/openbao/blob/main/LICENSE); Infisical's
  [releases](https://github.com/Infisical/infisical/releases) and [licence](https://github.com/Infisical/infisical/blob/main/LICENSE);
  the operator's [Google Secret Manager](https://external-secrets.io/latest/provider/google-secrets-manager/),
  [Vault](https://external-secrets.io/latest/provider/hashicorp-vault/) and
  [Infisical](https://external-secrets.io/latest/provider/infisical/) provider pages.
- GitHub: [deployments and environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments);
  [OpenID Connect](https://docs.github.com/en/actions/concepts/security/openid-connect); [Dependabot supported
  ecosystems](https://docs.github.com/en/code-security/dependabot/ecosystems-supported-by-dependabot/supported-ecosystems-and-repositories)
  and the [`dependabot.yml` options reference](https://docs.github.com/en/code-security/dependabot/dependabot-version-updates/configuration-options-for-the-dependabot.yml-file).
- just [1.59.0](https://github.com/casey/just/releases/tag/1.59.0) and [README](https://github.com/casey/just); Task
  [v3.54.0](https://github.com/go-task/task/releases/tag/v3.54.0).
- pre-commit: [PyPI](https://pypi.org/project/pre-commit/) (first release 2014); prek
  [v0.5.11](https://github.com/j178/prek/releases/tag/v0.5.11), [README](https://github.com/j178/prek) and
  [PyPI](https://pypi.org/project/prek/) (first release 2025-08-08); hk [v2.14.0](https://github.com/jdx/hk/releases/tag/v2.14.0)
  and [docs](https://hk.jdx.dev/); lefthook [v2.2.3](https://github.com/evilmartians/lefthook/releases/tag/v2.2.3); the
  [aqua registry](https://github.com/aquaproj/aqua-registry) (`pkgs/j178/prek`, `pkgs/jdx/hk`,
  `pkgs/evilmartians/lefthook`).
- Renovate: registry (44.262.2, 2026-11-25); [mise manager](https://docs.renovatebot.com/modules/manager/mise/);
  [Docker](https://docs.renovatebot.com/docker/).
- Base images: [distroless README](https://github.com/GoogleContainerTools/distroless) (signing, the debian13 images);
  `cosign tree` on the pinned digests; [Chainguard Containers overview](https://edu.chainguard.dev/chainguard/chainguard-images/overview/);
  [Docker Hardened Images](https://docs.docker.com/dhi/) and its [quickstart](https://docs.docker.com/dhi/get-started/).
