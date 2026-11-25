# Architecture decision records

Each file here records one design decision of the template: the context, the decision as built, its consequences
(good and bad), the alternatives, and links to the code and docs that implement it. They explain *why* the template
is the way it is, so an adopter can keep a decision knowingly or replace it on purpose.

The format is [Michael Nygard's](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions), with two
sections added: **Alternatives considered** and **Links**. Nygard's five parts (title, status, context, decision,
consequences) fit on one screen, which suits a template with many small decisions and a single owner; MADR's extra
fields (deciders, drivers, per-option pros and cons) would mostly repeat the context here. The added sections keep
the rejected options and the implementing files next to the decision.

The threat model, which several of these decisions serve, is in [Threat model](../threat-model.md).

## Index

| ADR | Title | Status | In one line |
|-----|-------|--------|-------------|
| [0001](./0001-github-only.md) | GitHub is the only forge and CI | Accepted | GitHub Actions on hosted runners and GHCR; Artifact Registry a documented future option, not wired up. |
| [0002](./0002-apache-2-licence.md) | The template is licensed Apache-2.0 | Accepted | Apache-2.0, with a dependency licence policy to match. |
| [0003](./0003-platform-iac-outside-the-template.md) | Platform infrastructure-as-code lives outside the template | Accepted | No IaC in the template; platform and service edge code live in your own IaC repository. |
| [0004](./0004-one-uvicorn-process-per-pod.md) | One Uvicorn process per Pod, no Gunicorn | Accepted | One process per Pod through one launcher; Kubernetes supervises, the HPA scales. |
| [0005](./0005-client-ip-from-trusted-proxies.md) | The client IP comes from one resolver that trusts listed proxies only | Accepted | Forwarding headers count only from `TRUSTED_PROXY_CIDRS`; one resolver, no Uvicorn proxy headers. |
| [0006](./0006-migrations-as-a-release-job.md) | Migrations run in a Job that is a plain release resource | Accepted | Not a Helm hook; Helm waits for it and readiness waits for the schema. |
| [0007](./0007-stdout-logging-no-collector.md) | Logs go to stdout, and the chart ships no log collector | Accepted | JSON to stdout; no file logs, no DaemonSet; the platform collects. |
| [0008](./0008-edge-modes.md) | Three edge modes, with Gateway API as the default | Accepted | `gateway` (default), `tunnel`, and `ingress` as a legacy opt-in. |
| [0009](./0009-retire-nginx-one-routes-list.md) | Retire the nginx tier; one routes list feeds every edge | Accepted | Next.js serves its own assets behind a CDN; one `edge.routes` list. |
| [0010](./0010-one-source-per-security-header.md) | One source per security header, and HSTS from one place per deployment | Accepted | Each layer sets its own headers; `edge.hsts` (auto, app, edge) picks the one HSTS source. |
| [0011](./0011-namespace-bootstrap-scoped-deployer.md) | An admin bootstrap creates the namespaces; the CI deployer is namespace-scoped | Accepted | Namespaces, quotas and RBAC come from a one-time admin step; the deployer has Helm verbs only. |
| [0012](./0012-cpu-requests-no-cpu-limits.md) | CPU requests without CPU limits; memory limit equals the request | Accepted | No CPU throttling; memory limit equal to request. |
| [0013](./0013-distroless-runtime-one-interpreter.md) | A distroless backend runtime with the builder's own interpreter | Accepted | Distroless Python 3.13, a Debian 13 builder with the same interpreter, a Python HEALTHCHECK. |
| [0014](./0014-debian-fixes-over-the-base.md) | Images may take Debian's fix before the base image ships it | Accepted | A `debian-fixes` stage lays Debian's fixed packages over the base until it catches up. |
| [0015](./0015-uv-for-python.md) | uv manages the Python project, its lock and its environments | Accepted | One hashed `uv.lock`, frozen installs, a seven-day `exclude-newer`. |
| [0016](./0016-slsa-build-l3-for-release-images.md) | Release images aim at SLSA Build L3 | Accepted | Only the reusable build workflow pushes, signs and attests images, and the chart workflow signs the chart; the hardening lint enforces it. |
| [0017](./0017-release-builds-use-no-cache.md) | Release builds use no build cache | Accepted | No cache anywhere on the release path, enforced fail-closed. |
| [0018](./0018-cosign-keyless-and-kyverno-admission.md) | Admission verifies keyless cosign signatures and provenance with Kyverno | Accepted | Example Kyverno policies check the build identity, provenance and Rekor; no policy-controller. |
| [0019](./0019-attestations-and-release-assets.md) | Attestations in the registry, SBOMs and VEX as release assets | Accepted | Provenance and SBOM attestations pushed to the registry; SBOMs, VEX and digests on the release. |
| [0020](./0020-deploy-by-digest.md) | Every environment deploys by digest | Accepted | Digests flow from the build to every deploy and are checked after it. |
| [0021](./0021-keyless-deploys-per-environment.md) | Keyless deploys through Workload Identity Federation, one deployer per environment | Accepted | OIDC to WIF pinned by owner, repository and workflow; DNS endpoint; one deployer per environment. |
| [0022](./0022-repository-settings-as-a-script.md) | Repository settings ship as a script, with rules for a single owner | Accepted | `github-settings.sh` sets rulesets, pinning and environments; 0 approvals for a single owner, no bypass actor. |
| [0023](./0023-release-flow.md) | Releases are cut by release-please with a GitHub App token, as drafts with forced tags | Accepted | App token from the `release` environment; drafts with forced tags; the tag is the version. |
| [0024](./0024-signed-chart-from-its-own-workflow.md) | The chart is published and signed from its own workflow, at a repository-scoped path | Accepted | `reusable-chart-release.yml`, on `v*` tags, to `ghcr.io/<owner>/<repo>/charts`. |
| [0025](./0025-renovate-for-updates.md) | Renovate proposes every version update, after a seven-day wait | Accepted | Regex managers for every pin; Dependabot for security updates only. |
| [0026](./0026-blocking-pr-security-gates.md) | CodeQL and dependency review block pull requests | Accepted | Both blocking; the licence allowlist comes from `grant.yaml`. |
| [0027](./0027-api-contract-and-versioning.md) | The API contract is checked on every pull request, under a versioning policy | Accepted | oasdiff blocks breaking changes without the `breaking-change` label, which requires a `!` title (a major bump); Deprecation and Sunset headers. |
| [0028](./0028-frozen-supply-chain-scripts.md) | The core supply-chain scripts are frozen; new logic goes under `local/` | Accepted | Eight `infra/security` scripts change only by recorded exception. |
| [0029](./0029-auth-modes.md) | OIDC and Cloudflare Access are the first-class auth modes | Accepted | `oidc` and `cloudflare-access` for production; `gateway` and `bearer` are reference modes. |
| [0030](./0030-environments-dev-qa-production.md) | Three environments, dev, qa and production, with qa optional | Accepted | dev, then qa, then production on tags; `DEPLOY_ENVS` can drop qa. |
| [0031](./0031-optional-pr-previews.md) | Optional pull-request previews of the chart, with main-built images, in one namespace | Accepted | Off by default; same-repository PRs; main-built images; one namespace, a release per PR. |
| [0032](./0032-waf-csp-report-exception.md) | A narrow WAF exception lets CSP reports through | Accepted | On the report route only, two content types skip body inspection. |
| [0033](./0033-kubernetes-floor-1-34.md) | Kubernetes 1.34 is the tested floor | Accepted | The kind matrix runs the floor (1.34) and kind's newest node image. |
| [0034](./0034-helm-4.md) | Deploys use Helm 4; Helm 3 stays pinned for manual checks | Accepted | Pinned Helm 4 for deploys and CI; Helm 3 pinned for a manual render, not re-proven in CI. |
| [0035](./0035-kind-default-local-cluster.md) | kind is the default local cluster, matching CI | Accepted | `make kind-up` and `make kind-deploy` match CI; minikube optional. |
| [0036](./0036-two-monitoring-flavours.md) | Alert rules and scrapes in two flavours, Prometheus Operator and GKE Managed Prometheus | Accepted | `monitoring.flavour`: Prometheus Operator objects or GMP `PodMonitoring`, `Rules` and `ClusterRules`. |
| [0037](./0037-weekly-rescan-scope.md) | The weekly re-scan covers what runs and the latest release | Accepted | Deployed digests and the latest release, weekly. |
| [0038](./0038-generic-names-only.md) | The template and its guides use generic names only | Accepted | Placeholders only; tests keep the owner's names and private paths out. |
| [0039](./0039-copier-deferred.md) | Template upgrades are not Copier-based yet | Deferred | Upstream sync by merge now; revisit Copier under stated conditions. |
| [0040](./0040-optional-ai-pr-review.md) | An optional, advisory AI reviewer for pull requests | Accepted | PR-Agent, off by default, advisory, minimal rights ([`pr-agent.yml`](../../.github/workflows/pr-agent.yml)). |
| [0041](./0041-documentation-site.md) | A curated documentation site, built with Starlight and published to GitHub Pages | Accepted | `docs-site/`: curated pages, the walkthroughs loaded from their files, a strict build, Pages from `main`. |
| [0042](./0042-one-hostname-per-environment.md) | One hostname per environment | Accepted | `edge.hosts.root` serves the whole app (`<service>[-<env>].<domain>`); `edge.hosts.api` is an optional backend-only host; there is no `app` host. |
| [0043](./0043-mise-for-the-developer-toolchain.md) | mise for the developer toolchain | Accepted | mise owns the CLI tools, Node and uv from `mise.toml` and a committed `mise.lock`; `scripts/tools.sh` installs the pinned mise first, repo-local and hermetic; uv and npm keep the app's packages. |
| [0044](./0044-prek-for-git-hooks.md) | prek for the git hooks | Accepted | prek, a single binary read from `mise.toml` and `mise.lock`, runs the hooks of `.pre-commit-config.yaml` (all local); the old runner's row and requirements are gone. prek is pre-1.0. |
| [0045](./0045-pnpm.md) | pnpm for the frontend and the docs site | Accepted | pnpm, the standalone release from `mise.toml` and `mise.lock`, installs both projects with `--frozen-lockfile` under a seven-day release age checked against the lock, strict build-script failures, the exotic-source block and `trustPolicy: no-downgrade` |

A status is **Accepted** (in force; a note may follow, such as "not yet implemented"), **Deferred** (considered and
postponed, with the conditions to revisit) or **Superseded** (replaced; it names the ADR that replaced it).

## How to add or supersede an ADR

These records are yours once you adopt the template: keep them, supersede them, add your own.

1. **Add one.** Copy [`0000-template.md`](./0000-template.md) to `NNNN-<slug>.md` with the next free number, fill
   every section, and add its row to the index above. One decision per file; link only files that exist.
2. **Supersede one.** Write the new ADR, then change the old one's status to `Superseded by [ADR-NNNN](...)` and the
   status cell of its row here to the single word `Superseded`. Leave the old text as it was: it records why the
   earlier choice made sense.
3. **Defer one.** Status `Deferred`, with the conditions under which to revisit it in the decision.
4. **Keep them true.** An ADR states the decision as built; when the code changes the decision, the same pull request
   updates or supersedes the ADR. `tests/test_adr_index.py` checks the sections, the status and this index.
