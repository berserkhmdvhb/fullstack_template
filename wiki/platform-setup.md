# Platform setup: start here

The platform runs on three providers. This page says what each one is for, the order to set them up in, and where every resulting value lives. The step-by-step work is in four guides:

1. [GKE Cluster Setup](./gke-cluster-setup.md): the Kubernetes cluster, the image registry, and keyless access from GitHub Actions.
2. [Cloudflare Setup](./cloudflare-setup.md): the domains, the edge, Access, the Tunnel into the cluster with its token in Secret Manager, the service's routes, private previews, and off-site backups.
3. [GitHub Pages Setup](./github-pages-setup.md): the documentation site on GitHub Pages, with an optional custom domain.
4. [PR-Agent Setup](./pr-agent-setup.md): the optional, advisory AI reviewer, its key and its spend limit.

## How to read this guide

> Every section of the four guides starts with one of three tags. They tell you what is safe to do by hand today.
>
> - **Do now.** Stable. Do it by hand; nothing later replaces it.
> - **Planned change** — what changes. The step is right today, but a later change replaces or extends it. Read the note before relying on it.
> - **Managed as code** — an infrastructure-as-code repository may create this; if you do it by hand you'll need an import (`tofu import`) or a rebuild later. It's a choice: let the code create it, or build by hand now and import afterwards.

## What each provider owns

> **Do now.**

| Provider | Owns | Why there |
|---|---|---|
| **Google Cloud** | the GKE cluster, optionally Artifact Registry, and the service identities for nodes and CI | managed Kubernetes with keyless CI login, enforced NetworkPolicies, and signed-image admission later |
| **Cloudflare** | the domain and DNS, edge TLS, WAF, the Tunnel, Access, static hosting, and R2 backups | one home for everything public, no open inbound port on the cluster, and a free tier for most of it |
| **GitHub** | code, CI/CD, approvals, and the environment-scoped secrets | the pipeline, plus the record of who approved what |

## The order

> **Do now** for rows 0 to 5. **After the first push** for rows 6 to 9, and row 10 after the first deploy.

| Step | What | Where | Takes |
|---|---|---|---|
| 0 | Two-factor sign-in on all three accounts, and a budget alert on Google Cloud | GKE §4, Cloudflare §4, GitHub settings | 15 min |
| 1 | The cluster and its keyless access, through to the laptop-side proof of the provider (the `check` run comes after the first push, step 6; 9.4 is a decision: on the Tunnel, skip its Gateway) | [GKE §1 to §13](./gke-cluster-setup.md) (12.5 belongs to the optional previews row); production can have a project of its own ([GKE §2.4](./gke-cluster-setup.md#24-production-in-its-own-project)) | 60 to 90 min |
| 2 | The app's secrets: the External Secrets Operator from its pinned chart, one identity and store per namespace that reads only its environment's secrets, the secrets in Secret Manager, and the check that dev cannot read production's (the `ExternalSecret`s' own check comes after the first deploy) | [GKE §14](./gke-cluster-setup.md#14-the-apps-secrets-external-secrets-with-secret-manager) | 30 min |
| 3 | The domains (a production domain per project, one lab domain), the zone baseline, Zero Trust, the tunnel and its connector, the lab Access application, `EDGE_DOMAIN` and the routes, through to the deploy-token check. The tunnel token goes to Secret Manager first, so step 2's External Secrets Operator must be running | [Cloudflare §1 to §11](./cloudflare-setup.md) | 1.5 to 2.5 hours |
| 4 | Optional, for a site repository only: off-site backups | [Cloudflare §12](./cloudflare-setup.md#12-optional-a-private-r2-bucket-for-off-site-backups) | 15 min |
| 5 | Set the values of the table below in GitHub. Variables, and the secrets only where the table says so. | GitHub settings | 5 min |
| 6 | After the first push: a green `deploy-ops` `check` per environment | [GKE §13.3](./gke-cluster-setup.md#133-the-first-check-deploy-ops-check) | 10 min |
| 7 | After the first push and the settings script: the documentation site on GitHub Pages, and optionally its custom domain | [GitHub Pages §2 to §5](./github-pages-setup.md) | 10 min, plus the certificate |
| 8 | Optional: the AI reviewer | [PR-Agent §2 to §8](./pr-agent-setup.md) | 15 min |
| 9 | After the first push, optional: pull request previews (their wildcard Access application needs the lab domain of step 3) | [GKE §12.5](./gke-cluster-setup.md#125-optional-pull-request-previews), [Cloudflare §9.2](./cloudflare-setup.md#92-optional-the-previews-hostnames-access-application-and-service-token), [CI/CD](./ci-cd.md#pull-request-previews) | 30 min |
| 10 | After the first deploy, optional: the weekly live-drift check, its read-only identity per environment and the `drift-<env>` GitHub environments | [GKE §12.6](./gke-cluster-setup.md#126-optional-the-live-drift-identity), [CI/CD](./ci-cd.md#live-drift) | 20 min |

Steps 2 and 3 need step 1: the operator and the Tunnel run inside the cluster. Steps 1 and 3 each end with a checklist. Don't start the next step until every check in the previous one passes.

## Variables and secrets

> The repository variables and environments are set by [scripts/github-settings.sh](../scripts/github-settings.sh); see [GitHub repository settings](./github-repository-settings.md).

This is the single list. Every `vars.*` and `secrets.*` that a template workflow reads is here, and the GKE guide links to it rather than repeating it. "Environment" means a GitHub environment (`dev`, `qa`, `production`, `preview` if you turn the pull request previews on: it takes the same cluster variables, with its own deployer and provider, and `drift-<env>` if you turn the live-drift check on: two variables of its own); "repository" means a repository-wide variable.

| Name | Kind | Scope | Purpose | Comes from |
|---|---|---|---|---|
| `GCP_WIF_PROVIDER` | variable | **environment**, all three (same value) | the Workload Identity provider the job logs in through | [GKE §13.1](./gke-cluster-setup.md#131-variables-and-secrets) |
| `GCP_DEPLOYER_SA` | variable | **environment**, a **different** service account in each | the deployer this environment acts as (`gh-deployer-<env>@<PROJECT_ID>.iam.gserviceaccount.com`) | [GKE §12.3](./gke-cluster-setup.md#123-one-deployer-service-account-per-environment) |
| `GCP_PROJECT_ID`, `GKE_CLUSTER`, `GKE_LOCATION` | variable | repository; an environment value overrides it | which cluster the environment deploys to | [GKE §13.1](./gke-cluster-setup.md#131-variables-and-secrets) |
| `DEPLOY_ENABLED` | variable | repository | `true` turns deploys on; unset (the default) skips them, with a notice in the run summary. Set it only after the first successful `check` ([GKE §13.3](./gke-cluster-setup.md#133-the-first-check-deploy-ops-check)), and not before the platform rollout | you |
| `DEPLOY_ENVS` | variable | repository | optional comma list without spaces, e.g. `dev,production`; leave `qa` out to skip it. Unset runs all three. `dev` and `production` always follow the chain rules | you |
| `DEPLOY_MODE` | variable | environment | `gke` (the default, Workload Identity Federation) or `kubeconfig` (see below) | you |
| `DEPLOY_URL` | variable | environment | optional: the URL the environment's deployment record links (its public app URL); GitHub shows it on the run and in the environment's deployment history, with each deploy's status ([Deploy chain](./ci-cd.md#deploy-chain)). Unset records the deployment without a link | you |
| `KUBECONFIG_B64` | **secret** | environment, only for `DEPLOY_MODE=kubeconfig` | the base64 kubeconfig; the job decodes it into a 0600 file and deletes it | you |
| `SERVICE_NAME` | variable | repository | the Helm release name (default: the chart name) **and** the namespace prefix: the app namespace is `<SERVICE_NAME>-<env>` | you |
| `HELM_TIMEOUT` | variable | repository | optional: the Helm wait timeout (default `5m`) | you |
| `HISTORY_KEEP` | variable | repository | optional: the revision count that the `cleanup` report flags (default `5`) | you |
| `BUILD_ARM64` | variable | repository | optional: `true` also builds each image for linux/arm64, on a native arm runner, and merges it with amd64 into one index; unset builds linux/amd64 only. On a private repository the arm minutes are billed ([details](./github-repository-settings.md#the-vars-file)) | you |
| `SCORECARD_ENABLED` | variable | repository | optional: set to `false` on a private copy to skip the OpenSSF Scorecard job (it is meant for public repositories); unset by default, which runs it | you |
| `CODE_SECURITY` | variable | repository | optional: set to `off` only on a private copy without GitHub code security (CodeQL and dependency review skip); unset by default, which runs them | you |
| `PREVIEWS_ENABLED` | variable | repository | optional: `true` turns on the pull request previews ([CI/CD](./ci-cd.md#pull-request-previews)); unset (the default) skips every preview job | you |
| `PREVIEW_TTL_DAYS` | variable | repository | optional: the daily sweep removes a preview last deployed this many days ago (default `7`) | you |
| `EDGE_DOMAIN` | variable | **environment**: `dev`, `qa`, `production` (and `drift-<env>` when the live-drift check is on) | the zone of that environment's one hostname, composed at deploy time: `<SERVICE_NAME>-<env>.<EDGE_DOMAIN>` for dev and qa, `<SERVICE_NAME>.<EDGE_DOMAIN>` for production. Dev and qa take the lab domain, production its own domain. The committed values keep their placeholder host, so no real domain is committed; unset, the placeholder guard refuses the deploy | [Cloudflare §9.3](./cloudflare-setup.md#93-edge_domain-and-the-apps-settings) |
| `PREVIEW_DOMAIN` | variable | environment `preview` | optional: the zone of the previews' hostnames (`<SERVICE_NAME>-pr-<N>.<PREVIEW_DOMAIN>`); unset, previews get no public URL | you |
| `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET` | **secret** | environment `preview`, with `PREVIEW_DOMAIN` | the Cloudflare Access service token the smoke test sends through the preview's URL | [Cloudflare §9.2](./cloudflare-setup.md#92-optional-the-previews-hostnames-access-application-and-service-token) |
| `DRIFT_ENABLED` | variable | repository | optional: `true` turns on the weekly live-drift check ([CI/CD](./ci-cd.md#live-drift)); unset (the default) skips its drift and report jobs, with a summary line, so no `drift-<env>` environment is ever referenced | [GKE §12.6](./gke-cluster-setup.md#126-optional-the-live-drift-identity) |
| `DRIFT_WIF_PROVIDER` | variable | environment `drift-<env>` (you create it; optional) | the live-drift provider of that environment (`github-oidc-drift-<env>`); unset, the weekly drift run skips the environment with a summary line ([CI/CD](./ci-cd.md#live-drift)) | [GKE §12.6](./gke-cluster-setup.md#126-optional-the-live-drift-identity) |
| `DRIFT_SA` | variable | environment `drift-<env>`, a **different** service account in each | the read-only drift reader it acts as (`gh-drift-<env>@<PROJECT_ID>.iam.gserviceaccount.com`) | [GKE §12.6](./gke-cluster-setup.md#126-optional-the-live-drift-identity) |
| `PR_AGENT_ENABLED` | variable | repository | optional: `true` turns on the advisory AI reviewer, PR-Agent ([CI/CD](./ci-cd.md#ai-pull-request-review)); unset (the default) skips it | [PR-Agent §4](./pr-agent-setup.md#4-turn-it-on) |
| `ANTHROPIC_KEY` | **secret** | environment `ai-review` (you create it), only with `PR_AGENT_ENABLED` | the model provider's API key PR-Agent reviews with; give it a spend limit | [PR-Agent §2 and §3](./pr-agent-setup.md#3-the-ai-review-environment-and-its-secret) |
| `RELEASE_APP_ID` | variable | repository | the release GitHub App's ID: [`release-please.yml`](../.github/workflows/release-please.yml) mints its token with it, and the `release-tags` ruleset names it as the one actor that may create a `v*` tag. Unset skips release-please, with a notice | [The release GitHub App](./github-repository-settings.md#the-release-github-app) |
| `RELEASE_APP_PRIVATE_KEY` | **secret** | environment `release` | the release GitHub App's private key | [The release GitHub App](./github-repository-settings.md#the-release-github-app) |
| GitHub Pages, Source: GitHub Actions, and the `github-pages` environment | setting | repository | the documentation site's deploy ([`docs-site.yml`](../.github/workflows/docs-site.yml)); no variable or secret: the deploy reads the address from Pages and proves itself with the run's OIDC token | [GitHub Pages §2 and §3](./github-pages-setup.md#2-turn-pages-on-with-source-github-actions) |
| `GITHUB_TOKEN` | secret | automatic | the workflow token; you set nothing | GitHub |

Google Cloud needs no secret at all: GitHub Actions logs in with a short-lived token through Workload Identity Federation. The environment variables above aren't sensitive; a job reads its environment's values only after the environment's branch policy (and, for `production`, its approval) has passed.

### Production in its own project

A `production` environment in a project and cluster of its own sets five environment-level variables, through `ENV_production_*` lines in the vars-file of [the settings script](./github-repository-settings.md#the-vars-file). The deploy jobs run with `environment:` set, so an environment variable overrides the repository one of the same name ([GitHub: variable precedence](https://docs.github.com/en/actions/learn-github-actions/variables)), and no workflow changes. Keep the repository-level `GCP_PROJECT_ID`, `GKE_CLUSTER` and `GKE_LOCATION` for `dev` and `qa`.

```text
GCP_PROJECT_ID=NONPROD_PROJECT_ID
GKE_CLUSTER=apps-dev
GKE_LOCATION=NONPROD_LOCATION
ENV_production_GCP_PROJECT_ID=PRODUCTION_PROJECT_ID
ENV_production_GKE_CLUSTER=apps-prod
ENV_production_GKE_LOCATION=PRODUCTION_LOCATION
ENV_production_GCP_WIF_PROVIDER=PRODUCTION_PROVIDER_RESOURCE_NAME
ENV_production_GCP_DEPLOYER_SA=gh-deployer-production@PRODUCTION_PROJECT_ID.iam.gserviceaccount.com
```

The setup that creates that project is [GKE §2.4](./gke-cluster-setup.md#24-production-in-its-own-project).

### Kubeconfig mode

`DEPLOY_MODE=kubeconfig` replaces the Google login for an environment, for a cluster that isn't GKE. In that environment, set the variable `DEPLOY_MODE` to `kubeconfig`, and the secret `KUBECONFIG_B64` to `base64 < kubeconfig | tr -d '\n'` (a kubeconfig whose user can only act in that environment's namespace). The `GCP_*` and `GKE_*` variables are then unused there. Run the `check` operation to prove it.

### Used by a site repository, not by the template

The template's workflows don't read these. They belong to your site repository's pipeline.

| Name | Kind | Scope | Purpose | Comes from |
|---|---|---|---|---|
| `CLOUDFLARE_ACCOUNT_ID` | variable | repository | the account the site deploy targets | [Cloudflare §10](./cloudflare-setup.md#10-static-hosting-for-your-site-prove-the-deploy-token) |
| `CLOUDFLARE_API_TOKEN` | **secret** | environment `production` | the scoped, expiring deploy token | [Cloudflare §7](./cloudflare-setup.md#7-api-tokens-scoped-expiring-and-tested) |
| R2 access key ID and secret key | **secret** | environment `production` | optional off-site backups | [Cloudflare §12](./cloudflare-setup.md#12-optional-a-private-r2-bucket-for-off-site-backups) |
| Tunnel token | **secret** | Secret Manager `cloudflared-tunnel-token`, synced into the Secret `cloudflared/tunnel-token` by the External Secrets Operator; never in GitHub and never a hand-made Secret | the connector's login | [Cloudflare §8.3](./cloudflare-setup.md#83-run-the-connector-in-the-cluster) |

### Optional: Artifact Registry only

**Not wired up yet.** The template pushes to GHCR; no workflow reads `vars.IMAGE_REGISTRY`, these two names, or an Artifact Registry login, and the provider condition admits no image-pushing workflow. They are listed only so you don't present them as required.

| Name | Kind | Scope | Purpose |
|---|---|---|---|
| `GCP_REGION`, `GCP_AR_REPOSITORY` | variable | repository | the registry's location and repository name (`apps`) |

### What a private repository needs

- **Public repository (any plan):** environments, their secrets and variables, branch and tag policies, and required reviewers are all available. The template's repository is public, so it gets the production approval gate at no cost.
- **Private repository:** GitHub Pro (personal) or Team (organisation) gives environments, their secrets and variables, and branch and tag policies. Required reviewers and wait timers need **GitHub Enterprise**, so a private copy on Pro or Team deploys with branch policies but **has no approval gate**. On Free, a private repository has no environments at all. There is no manual fallback.
- **Signing and attestations:** keyless image signing works on any repository, but it records the repository name, workflow ref and commit in the public Rekor log. Artifact attestations (build provenance and SBOM) need a public repository or GitHub Enterprise Cloud. On a private repository the release skips them with a warning unless you set the repository variable `ATTESTATIONS_ENABLED` to `true`; set it only when your plan supports them, or the release fails. How to verify: [Verifying an image](./security.md#verifying-an-image).

Sources: [Managing environments for deployment](https://docs.github.com/en/actions/managing-workflow-runs-and-deployments/managing-deployments/managing-environments-for-deployment) and [Deployments and environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments); details in [GKE §11.1](./gke-cluster-setup.md#111-public-or-private).

## The principles the guides follow

> **Do now.**

These are the rules the guides apply. The pipeline will follow the same ones.

- **No long-lived credentials where a keyless option exists.** GitHub logs in to Google Cloud with OIDC, and no service-account key is ever created.
- **Least privilege, one identity per job.** The nodes, the deployer and each Cloudflare token can each do one thing, in one place.
- **Every credential expires.** Set a TTL on each token and a reminder before it runs out.
- **Prove a credential against the real endpoint it will call,** not only a "verify" call.
- **No public origin.** The cluster has private nodes and no LoadBalancer; public traffic arrives only through Cloudflare.
- **Pin everything:**
  - GitHub Actions by full commit SHA;
  - container images by digest;
  - CLI tools such as Wrangler by exact version;
  - the Terraform/OpenTofu providers by major series.
- **Destructive operations are safe by default.** They start as a dry run, or show a reviewable plan first. They take a backup before deleting anything. They need an explicit confirmation, and a non-interactive run must pass a flag.
- **Secrets never reach a command line, a log or a file in Git.** They're read without echo and passed on standard input.
- **Every change is checked right away,** by a read-only verification that exits non-zero on failure, so CI can use it as a gate.
- **Identifiers stay out of the docs.** Account IDs, zone IDs and project numbers live in local env files and CI variables only.

## After the setup

> **Planned changes.**

Deploys stay off until the platform rollout. The work between now and then, in order:

1. **The first push** of the code to the empty repository.
2. **The settings script:** the `main` ruleset and a `v*` tag ruleset, the environments and their branch policies, the repository variables, the Actions policy, and the release GitHub App with its one-time steps ([The release GitHub App](./github-repository-settings.md#the-release-github-app)).
3. **The namespace and RBAC bootstrap:** the namespaces (with Pod Security labels, quota and LimitRange) and the deployer RBAC for each environment, rendered by `scripts/k8s-bootstrap.sh <env>` and applied by an admin ([GKE §13.2](./gke-cluster-setup.md#132-the-namespaces-and-the-deployer-rbac-come-from-the-bootstrap)). Don't create the namespaces by hand.
4. **A green `check`** for each environment ([GKE §13.3](./gke-cluster-setup.md#133-the-first-check-deploy-ops-check)); production's runs from the first `v*` tag.
5. **The image pull secret** in each app namespace, if your GHCR images are private ([GKE §13.4](./gke-cluster-setup.md#134-an-image-pull-secret-for-private-ghcr-images)).
6. **The platform rollout:** set `DEPLOY_ENABLED=true`, deploy dev, then qa, then production; close the public cluster endpoint.

A site pipeline can follow the template's.
