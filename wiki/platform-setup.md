# Platform setup: start here

The platform runs on three providers. This page says what each one is for, the order to set them up in, and where every resulting value lives. The step-by-step work is in two guides:

1. [GKE Cluster Setup](./gke-cluster-setup.md): the Kubernetes cluster, the image registry, and keyless access from GitHub Actions.
2. [Cloudflare Setup](./cloudflare-setup.md): the domain, the edge, the Tunnel into the cluster, private previews, and off-site backups.

## How to read this guide

> Every section of the three guides starts with one of three tags. They tell you what is safe to do by hand today.
>
> - **Do now.** Stable. Do it by hand; nothing later replaces it.
> - **Planned change** — what changes. The step is right today, but a later change replaces or extends it. Read the note before relying on it.
> - **Owned by lodestar-infra (L1.x)** — if you do this by hand you'll need `tofu import` or a rebuild later. It's a choice: apply lodestar-infra yourself, or build by hand now and import afterwards.

## What each provider owns

> **Do now.**

| Provider | Owns | Why there |
|---|---|---|
| **Google Cloud** | the GKE cluster, optionally Artifact Registry, and the service identities for nodes and CI | managed Kubernetes with keyless CI login, enforced NetworkPolicies, and signed-image admission later |
| **Cloudflare** | the domain and DNS, edge TLS, WAF, the Tunnel, Access, static hosting, and R2 backups | one home for everything public, no open inbound port on the cluster, and a free tier for most of it |
| **GitHub** | code, CI/CD, approvals, and the environment-scoped secrets | the pipeline, plus the record of who approved what |

## The order

> **Do now** for rows 0 to 4. **After the first push** for row 5.

| Step | What | Where | Takes |
|---|---|---|---|
| 0 | Two-factor sign-in on all three accounts, and a budget alert on Google Cloud | GKE §4, Cloudflare §4, GitHub settings | 15 min |
| 1 | The cluster and its keyless access, through to the laptop-side proof of the provider (the `check` run comes after the first push, step 5) | [GKE §1 to §13](./gke-cluster-setup.md) | 60 to 90 min |
| 2 | The domain and the edge, through to the deploy-token check | [Cloudflare §1 to §11](./cloudflare-setup.md) | 45 to 75 min |
| 3 | Optional, for the Kynosura repo only: off-site backups | [Cloudflare §12](./cloudflare-setup.md#12-optional-a-private-r2-bucket-for-off-site-backups) | 15 min |
| 4 | Set the values of the table below in GitHub. Variables, and the secrets only where the table says so. | GitHub settings | 5 min |
| 5 | After the first push: a green `deploy-ops` `check` per environment | [GKE §13.3](./gke-cluster-setup.md#133-the-first-check-deploy-ops-check) | 10 min |

Step 2 needs step 1, because the Tunnel runs inside the cluster. Steps 1 and 2 each end with a checklist. Don't start the next step until every check in the previous one passes.

## Variables and secrets

> The repository variables and environments are set by [scripts/github-settings.sh](../scripts/github-settings.sh); see [GitHub repository settings](./github-repository-settings.md).

This is the single list. Every `vars.*` and `secrets.*` that a template workflow reads is here, and the GKE guide links to it rather than repeating it. "Environment" means a GitHub environment (`dev`, `qa`, `production`); "repository" means a repository-wide variable.

| Name | Kind | Scope | Purpose | Comes from |
|---|---|---|---|---|
| `GCP_WIF_PROVIDER` | variable | **environment**, all three (same value) | the Workload Identity provider the job logs in through | [GKE §13.1](./gke-cluster-setup.md#131-variables-and-secrets) |
| `GCP_DEPLOYER_SA` | variable | **environment**, a **different** service account in each | the deployer this environment acts as (`gh-deployer-<env>@<PROJECT_ID>.iam.gserviceaccount.com`) | [GKE §12.3](./gke-cluster-setup.md#123-one-deployer-service-account-per-environment) |
| `GCP_PROJECT_ID`, `GKE_CLUSTER`, `GKE_LOCATION` | variable | repository; an environment value overrides it | which cluster the environment deploys to | [GKE §13.1](./gke-cluster-setup.md#131-variables-and-secrets) |
| `DEPLOY_ENABLED` | variable | repository | `true` turns deploys on; unset (the default) skips them, with a notice in the run summary. Set it only after the first successful `check` ([GKE §13.3](./gke-cluster-setup.md#133-the-first-check-deploy-ops-check)), and not before the platform rollout | you |
| `DEPLOY_ENVS` | variable | repository | optional comma list without spaces, e.g. `dev,production`; leave `qa` out to skip it. Unset runs all three. `dev` and `production` always follow the chain rules | you |
| `DEPLOY_MODE` | variable | environment | `gke` (the default, Workload Identity Federation) or `kubeconfig` (see below) | you |
| `KUBECONFIG_B64` | **secret** | environment, only for `DEPLOY_MODE=kubeconfig` | the base64 kubeconfig; the job decodes it into a 0600 file and deletes it | you |
| `SERVICE_NAME` | variable | repository | the Helm release name (default: the chart name) **and** the namespace prefix: the app namespace is `<SERVICE_NAME>-<env>` | you |
| `HELM_TIMEOUT` | variable | repository | optional: the Helm wait timeout (default `5m`) | you |
| `HISTORY_KEEP` | variable | repository | optional: the revision count that the `cleanup` report flags (default `5`) | you |
| `CODE_SECURITY` | variable | repository | optional: set to `off` only on a private copy without GitHub code security (CodeQL and dependency review skip); unset by default, which runs them | you |
| `GITHUB_TOKEN` | secret | automatic | the workflow token; you set nothing | GitHub |

Google Cloud needs no secret at all: GitHub Actions logs in with a short-lived token through Workload Identity Federation. The environment variables above aren't sensitive; a job reads its environment's values only after the environment's branch policy (and, for `production`, its approval) has passed.

### Kubeconfig mode

`DEPLOY_MODE=kubeconfig` replaces the Google login for an environment, for a cluster that isn't GKE. In that environment, set the variable `DEPLOY_MODE` to `kubeconfig`, and the secret `KUBECONFIG_B64` to `base64 < kubeconfig | tr -d '\n'` (a kubeconfig whose user can only act in that environment's namespace). The `GCP_*` and `GKE_*` variables are then unused there. Run the `check` operation to prove it.

### Used by the Kynosura repo, not by the template

The template's workflows don't read these. They belong to the Kynosura site repository's pipeline.

| Name | Kind | Scope | Purpose | Comes from |
|---|---|---|---|---|
| `CLOUDFLARE_ACCOUNT_ID` | variable | repository | the account the site deploy targets | [Cloudflare §10](./cloudflare-setup.md#10-static-hosting-for-kynosura-prove-the-deploy-token) |
| `CLOUDFLARE_API_TOKEN` | **secret** | environment `production` | the scoped, expiring deploy token | [Cloudflare §7](./cloudflare-setup.md#7-api-tokens-scoped-expiring-and-tested) |
| R2 access key ID and secret key | **secret** | environment `production` | optional off-site backups | [Cloudflare §12](./cloudflare-setup.md#12-optional-a-private-r2-bucket-for-off-site-backups) |
| Tunnel token | **secret** | Kubernetes Secret `cloudflared/tunnel-token`, never in GitHub | the connector's login | [Cloudflare §8](./cloudflare-setup.md#8-cloudflare-tunnel-into-gke) |

### Optional: Artifact Registry only

**Not wired up yet.** The template pushes to GHCR; no workflow reads `vars.IMAGE_REGISTRY`, these two names, or an Artifact Registry login, and the provider condition admits no image-pushing workflow. They are listed only so you don't present them as required.

| Name | Kind | Scope | Purpose |
|---|---|---|---|
| `GCP_REGION`, `GCP_AR_REPOSITORY` | variable | repository | the registry's location and repository name (`apps`) |

### What a private repository needs

- **Public repository (any plan):** environments, their secrets and variables, branch and tag policies, and required reviewers are all available. The template's repository is public, so it gets the production approval gate at no cost.
- **Private repository:** GitHub Pro (personal) or Team (organisation) gives environments, their secrets and variables, and branch and tag policies. Required reviewers and wait timers need **GitHub Enterprise**, so a private copy on Pro or Team deploys with branch policies but **has no approval gate**. On Free, a private repository has no environments at all. There is no manual fallback.

Sources: [Managing environments for deployment](https://docs.github.com/en/actions/managing-workflow-runs-and-deployments/managing-deployments/managing-environments-for-deployment) and [Deployments and environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments); details in [GKE §11.1](./gke-cluster-setup.md#111-public-or-private).

## The principles both guides follow

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
2. **The settings script:** the `main` ruleset and a `v*` tag ruleset, the four environments and their branch policies, the repository variables, the Actions policy, and the release GitHub App.
3. **A green `check`** for each environment ([GKE §13.3](./gke-cluster-setup.md#133-the-first-check-deploy-ops-check)); production's runs from the first `v*` tag.
4. **The namespace and RBAC bootstrap:** the namespaces (with Pod Security labels, quota and LimitRange) and the deployer RBAC for each environment. Don't create the namespaces by hand.
5. **The image pull secret** in each app namespace, if your GHCR images are private ([GKE §13.4](./gke-cluster-setup.md#134-an-image-pull-secret-for-private-ghcr-images)).
6. **The platform rollout:** set `DEPLOY_ENABLED=true`, deploy dev, then qa, then production; close the public cluster endpoint.

Kynosura's site pipeline follows the template's.
