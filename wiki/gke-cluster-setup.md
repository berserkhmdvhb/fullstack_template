# GKE cluster setup for the fullstack template

This guide builds the Google Kubernetes Engine (GKE) cluster that the template's GitHub pipeline deploys to. It's step 1 of [Platform Setup](./platform-setup.md). It also sets up the keyless link between GitHub Actions and Google Cloud.

Each section starts with a tag (**Do now**, **Planned change**, or **Owned by lodestar-infra**); [How to read this guide](./platform-setup.md#how-to-read-this-guide) explains them. Work through it top to bottom. Every step ends with a **Check** that tells you what you should see. Don't go on to the next step until the check passes.

| | |
|---|---|
| Time | 60 to 90 minutes, about 15 of it waiting for Google Cloud |
| Cost | about $40 to $120 a month while the cluster runs (see [Costs](#14-costs-pausing-and-teardown)) |
| You need | a Google Cloud project with billing, a GitHub account and a Linux or macOS terminal |
| Matches | the GitHub Actions workflows in `.github/workflows/`. Written 2026-11-20. Cloud CLIs change, so each step checks your own tools before it relies on them. |

## Contents

1. [What you will build](#1-what-you-will-build)
2. [Before you start](#2-before-you-start)
3. [Set your variables once](#3-set-your-variables-once)
4. [Guard rails: billing and a budget](#4-guard-rails-billing-and-a-budget)
5. [Enable the APIs](#5-enable-the-apis)
6. [Network: a private VPC with Cloud NAT](#6-network-a-private-vpc-with-cloud-nat)
7. [A minimal identity for the nodes](#7-a-minimal-identity-for-the-nodes)
8. [Create the cluster](#8-create-the-cluster)
9. [Connect from your laptop and prove the basics](#9-connect-from-your-laptop-and-prove-the-basics)
10. [The image registry](#10-the-image-registry)
11. [The GitHub repository](#11-the-github-repository)
12. [Keyless access for GitHub Actions](#12-keyless-access-for-github-actions)
13. [The end-to-end check from GitHub](#13-the-end-to-end-check-from-github)
14. [Costs, pausing and teardown](#14-costs-pausing-and-teardown)
15. [Final checklist and the values to hand over](#15-final-checklist-and-the-values-to-hand-over)
16. [Troubleshooting](#16-troubleshooting)
17. [What comes next](#17-what-comes-next)

---

## 1. What you will build

> **Do now.**

```text
 GitHub Actions (your repo only)
        │  1. GitHub issues a short-lived OIDC token for this one workflow run
        ▼
 Workload Identity Federation pool "github"
        │  2. Google checks the token: right owner ID, right repository ID,
        │     and a workflow file that is deploy.yml or deploy-ops.yml on main or a v* tag
        ▼
 One service account per environment    (no key file exists, ever)
   gh-deployer-dev / -qa / -production  (each only for its own GitHub environment)
        ├──► GKE cluster "apps-dev"     reach the control plane through the DNS-based endpoint;
        │                                deploy rights are Kubernetes RBAC, one Role per app namespace
        └──► Artifact Registry "apps"   optional: push images, only if you don't use GHCR

 GKE cluster "apps-dev" (Standard, one zone)
   • private nodes: no public IP addresses; outbound traffic goes through Cloud NAT
   • Dataplane V2: NetworkPolicies are enforced
   • Workload Identity: Pods get cloud permissions without keys
   • Gateway API: the replacement for the retired ingress-nginx
   • nodes run as gke-nodes, a service account that can write logs and metrics
     and pull from the registry, and nothing else
```

**Why this design:**
- **No stored credentials.** Nothing long-lived can leak: GitHub proves who it is on every run, and the token expires within the hour.
- **Least privilege.** The nodes and the pipeline each get their own identity, with only the rights they need.
- **Private nodes.** Nothing on the internet can reach a node directly.
- **A Standard cluster.** This guide builds one. The chart no longer ships a log collector (the one thing that needed host access), so Autopilot is no longer ruled out by the chart; this guide has not been tried on it.

## 2. Before you start

> **Do now.**

### 2.1 Tools

| Tool | Install | Check |
|---|---|---|
| Google Cloud CLI | <https://cloud.google.com/sdk/docs/install> | `gcloud version` |
| GKE auth plugin | `gcloud components install gke-gcloud-auth-plugin`, or with apt: `sudo apt install google-cloud-cli-gke-gcloud-auth-plugin` | `gke-gcloud-auth-plugin --version` |
| kubectl | `gcloud components install kubectl`, or apt `kubectl` | `kubectl version --client` |
| curl, python3, git, jq | your package manager | `curl --version; python3 --version; git --version; jq --version` |

If you installed the CLI some months ago, update it with `gcloud components update`, or with your package manager if you installed it through apt.

### 2.2 Accounts and rights

- **A Google Cloud project with billing linked.** You need the **Owner** role on it. That's normal for a personal project.
- **A GitHub account.** It can be the owner of the repository, or an organisation where you're an admin.

### 2.3 Decisions to make now

| Decision | Default in this guide | Notes |
|---|---|---|
| Region and zone | `europe-west1` / `europe-west1-b` | Pick one close to you. Every resource below uses the same region. |
| Cluster name | `apps-dev` | It hosts the template's dev environment, and later Kynosura. |
| GitHub repository | `<you>/fullstack-template` | Created in [step 11](#11-the-github-repository). |
| Repository visibility | **public** (recommended) | See [11.1](#111-public-or-private). It changes which GitHub security features you get for free. |
| Spot nodes | off | Spot costs about 60 to 70 % less, but Google can reclaim a node at any time. That's fine for dev, but not for production. |

## 3. Set your variables once

> **Do now.**

Every later step reads these. Create the file once, then load it in each new terminal.

```bash
cat > ~/gke-setup.env <<'EOF'
export PROJECT_ID="your-project-id"          # gcloud projects list
export REGION="europe-west1"
export ZONE="europe-west1-b"
export CLUSTER="apps-dev"
export NETWORK="apps-vpc"
export SUBNET="apps-dev-subnet"
export AR_REPO="apps"
export GH_OWNER="your-github-user"           # or your organisation
export GH_REPO_NAME="fullstack-template"
export GH_REPO="$GH_OWNER/$GH_REPO_NAME"
# A separate kubeconfig, so this cluster never touches your local kind/minikube contexts.
export KUBECONFIG="$HOME/.kube/gke-apps-dev.yaml"
EOF
source ~/gke-setup.env
gcloud auth login                       # opens a browser; skip it if you're already logged in
gcloud config set project "$PROJECT_ID"
export PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
echo "PROJECT_NUMBER=$PROJECT_NUMBER" >> ~/gke-setup.env
```

**Check:**

```bash
source ~/gke-setup.env
gcloud config get-value project          # → your-project-id
echo "$PROJECT_NUMBER"                   # → a 10- to 12-digit number
gcloud auth list --filter=status:ACTIVE --format='value(account)'   # → your Google account
```

## 4. Guard rails: billing and a budget

> **Do now.**

Set the budget before you create anything that costs money.

1. In the console, open **Billing → Budgets & alerts → Create budget**.
2. **Scope:** this project only.
3. **Amount:** a specified amount, for example **$100 a month**.
4. **Alerts:** at **50 %, 90 % and 100 %** of actual spend, plus **100 % of forecasted** spend. Email the billing admins, which is you.

A budget alerts you; it doesn't stop spending. [Section 14](#14-costs-pausing-and-teardown) shows how to pause the cluster.

**Check:**

```bash
gcloud billing projects describe "$PROJECT_ID" --format='value(billingEnabled)'   # → True
```

Also confirm the budget appears in the console list.

## 5. Enable the APIs

> **Do now.** No lodestar-infra resource enables the APIs, so this stays a manual step.

```bash
gcloud services enable \
  compute.googleapis.com container.googleapis.com artifactregistry.googleapis.com \
  iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com \
  cloudresourcemanager.googleapis.com
```

This takes about a minute.

**Check:**

```bash
gcloud services list --enabled --format='value(config.name)' | grep -E \
 '^(compute|container|artifactregistry|iam|iamcredentials|sts|cloudresourcemanager)\.googleapis\.com$' | wc -l
# → 7
```

## 6. Network: a private VPC with Cloud NAT

> **Owned by lodestar-infra (L1.2).** You can do this by hand, or leave it to lodestar-infra. If you do it by hand, you'll need `tofu import` or a rebuild later.

A dedicated network keeps the cluster isolated from anything else in the project. The nodes have no public IP addresses, so they need two things:
- **Private Google Access,** to reach Google services such as Artifact Registry;
- **Cloud NAT,** to pull public images, for example from Docker Hub.

```bash
gcloud compute networks create "$NETWORK" --subnet-mode=custom

gcloud compute networks subnets create "$SUBNET" \
  --network="$NETWORK" --region="$REGION" \
  --range=10.10.0.0/20 \
  --secondary-range=pods=10.20.0.0/16,services=10.30.0.0/20 \
  --enable-private-ip-google-access

gcloud compute routers create apps-router --network="$NETWORK" --region="$REGION"
gcloud compute routers nats create apps-nat --router=apps-router --region="$REGION" \
  --auto-allocate-nat-external-ips --nat-all-subnet-ip-ranges \
  --enable-logging --log-filter=ERRORS_ONLY
```

The ranges are private addresses, and the secondary ranges give Pods and Services their own blocks. Change them only if they clash with a network you connect to later.

**Check:**

```bash
gcloud compute networks subnets describe "$SUBNET" --region="$REGION" \
  --format='value(privateIpGoogleAccess,secondaryIpRanges[].rangeName)'
# → True  pods;services   (the separator can vary)
gcloud compute routers nats describe apps-nat --router=apps-router --region="$REGION" \
  --format='value(name,sourceSubnetworkIpRangesToNat)'
# → apps-nat  ALL_SUBNETWORKS_ALL_IP_RANGES
```

## 7. A minimal identity for the nodes

> **Owned by lodestar-infra (L1.2).** You can do this by hand, or leave it to lodestar-infra. If you do it by hand, you'll need `tofu import` or a rebuild later.

By default, GKE nodes use the Compute Engine default service account, which usually holds **Editor** on the whole project. Replace it with one that can only write logs and metrics and pull images.

```bash
gcloud iam service-accounts create gke-nodes --display-name="GKE nodes (apps-dev)"
export NODE_SA="gke-nodes@$PROJECT_ID.iam.gserviceaccount.com"
echo "export NODE_SA=\"$NODE_SA\"" >> ~/gke-setup.env
```

First check whether your project offers Google's ready-made node role:

```bash
gcloud iam roles describe roles/container.defaultNodeServiceAccount --format='value(title)'
```

- **If that prints a title**, grant the role:

  ```bash
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --condition=None \
    --member="serviceAccount:$NODE_SA" --role="roles/container.defaultNodeServiceAccount"
  ```

- **If it says the role doesn't exist**, grant the four classic node roles instead:

  ```bash
  for role in roles/logging.logWriter roles/monitoring.metricWriter \
              roles/monitoring.viewer roles/stackdriver.resourceMetadata.writer; do
    gcloud projects add-iam-policy-binding "$PROJECT_ID" --condition=None \
      --member="serviceAccount:$NODE_SA" --role="$role" --format=none
  done
  ```

The right to pull images comes in [step 10](#10-the-image-registry), granted on the registry itself rather than on the whole project.

**Check:**

```bash
gcloud projects get-iam-policy "$PROJECT_ID" --flatten='bindings[].members' \
  --filter="bindings.members:serviceAccount:$NODE_SA" --format='value(bindings.role)'
# → the node role, or the four classic roles. Nothing else: no editor, no owner.
```

## 8. Create the cluster

> **Owned by lodestar-infra (L1.3).** You can do this by hand, or leave it to lodestar-infra. If you do it by hand, you'll need `tofu import` or a rebuild later. The cluster is the costliest thing to import; for dev, a rebuild is simpler.

### 8.1 Check that your gcloud knows every flag

```bash
for f in --enable-private-nodes --enable-dataplane-v2 --gateway-api --workload-pool \
         --enable-managed-prometheus --maintenance-window-recurrence --shielded-secure-boot \
         --cluster-secondary-range-name --enable-dns-access --spot; do
  gcloud container clusters create --help 2>/dev/null | grep -q -- "$f" \
    && echo "ok       $f" || echo "MISSING  $f   (update gcloud, then rerun this check)"
done
```

**Check:** every line starts with `ok`.

### 8.2 Create it

This takes about 8 to 12 minutes.

```bash
gcloud container clusters create "$CLUSTER" \
  --location="$ZONE" \
  --release-channel=regular \
  --network="$NETWORK" --subnetwork="$SUBNET" \
  --enable-ip-alias \
  --cluster-secondary-range-name=pods --services-secondary-range-name=services \
  --enable-private-nodes \
  --enable-dns-access \
  --enable-dataplane-v2 \
  --workload-pool="$PROJECT_ID.svc.id.goog" \
  --gateway-api=standard \
  --service-account="$NODE_SA" \
  --machine-type=e2-standard-2 --disk-type=pd-balanced --disk-size=50 \
  --num-nodes=2 --enable-autoscaling --min-nodes=1 --max-nodes=3 \
  --shielded-secure-boot --shielded-integrity-monitoring \
  --logging=SYSTEM,WORKLOAD --monitoring=SYSTEM --enable-managed-prometheus \
  --maintenance-window-start=2026-11-21T02:00:00Z \
  --maintenance-window-end=2026-11-21T06:00:00Z \
  --maintenance-window-recurrence='FREQ=WEEKLY;BYDAY=SA,SU' \
  --labels=env=dev,app=platform
  # For dev on spot nodes, add:  --spot
```

What the flags are for:

| Flag | Why |
|---|---|
| `--release-channel=regular` | Google patches and upgrades the cluster for you, at a tested pace. |
| `--enable-private-nodes` | The nodes get no public IP address. |
| `--enable-dns-access` | The DNS-based control-plane endpoint. CI reaches the cluster through it with IAM only, with no IP allowlist. Needed now, not later. |
| `--enable-dataplane-v2` | NetworkPolicies are enforced, and the chart relies on them. |
| `--workload-pool` | Workload Identity: Pods use cloud APIs without key files. |
| `--gateway-api=standard` | Gateway API resources (`Gateway`, `HTTPRoute`), backed by Google's load balancers. |
| `--service-account` | The nodes run as the minimal account from step 7. |
| `--shielded-*` | Secure boot and integrity monitoring on the nodes. |
| `--maintenance-window-*` | Upgrades happen only on weekend nights (UTC). Choose your own hours. |

Both flags, `--enable-dns-access` and `--no-enable-ip-access`, are described in GKE's [network isolation how-to](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/latest/network-isolation). The public IP endpoint stays on for now: closing it with `--no-enable-ip-access` waits for the later platform rollout, and you never put a CIDR allowlist on it for CI.

If the command asks for `--master-ipv4-cidr`, your gcloud or GKE version still uses the older private-cluster model. Add `--master-ipv4-cidr=172.16.0.32/28` and run it again.

**Check:**

```bash
gcloud container clusters describe "$CLUSTER" --location="$ZONE" --format="yaml(
  status, releaseChannel.channel, workloadIdentityConfig.workloadPool,
  networkConfig.datapathProvider, networkConfig.gatewayApiConfig.channel,
  controlPlaneEndpointsConfig.dnsEndpointConfig,
  nodePools[0].config.serviceAccount, nodePools[0].autoscaling)"
```

Expected:
- `status: RUNNING`;
- `channel: REGULAR`;
- `workloadPool: <project>.svc.id.goog`;
- `datapathProvider: ADVANCED_DATAPATH`;
- the gateway channel `CHANNEL_STANDARD`;
- `serviceAccount: gke-nodes@…`;
- `autoscaling.enabled: true`;
- `dnsEndpointConfig` with a `endpoint:` host name and `allowExternalTraffic: true`.

## 9. Connect from your laptop and prove the basics

> **Do now.**

```bash
source ~/gke-setup.env              # sets KUBECONFIG to the separate file
gcloud container clusters get-credentials "$CLUSTER" --location="$ZONE"
```

### 9.1 Nodes and versions

```bash
kubectl get nodes -o wide
```

**Check:** 2 nodes, `Ready`, with `<none>` in the EXTERNAL-IP column (that means private nodes).

### 9.2 Gateway API is installed

```bash
kubectl get gatewayclass
```

**Check:** the list includes `gke-l7-global-external-managed`, plus a few other `gke-l7-*` classes.

### 9.3 Cloud NAT works, and NetworkPolicy is enforced

This test pulls two public images, which proves outbound traffic works through NAT. It then shows that a deny policy actually blocks traffic.

```bash
kubectl create namespace np-test
kubectl -n np-test run web --image=nginxinc/nginx-unprivileged:stable-alpine --port=8080 --labels=app=web
kubectl -n np-test expose pod web --port=8080
kubectl -n np-test wait --for=condition=Ready pod/web --timeout=180s

# A. With no policy, a client can reach web:
kubectl -n np-test run probe-a --rm -i --restart=Never --image=busybox:1.37 -- \
  wget -qO- -T 5 http://web:8080 | grep -o '<title>.*</title>'

# B. Deny all ingress, then try again:
kubectl -n np-test apply -f - <<'EOF'
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: { name: default-deny-ingress }
spec: { podSelector: {}, policyTypes: [Ingress] }
EOF
kubectl -n np-test run probe-b --rm -i --restart=Never --image=busybox:1.37 -- \
  wget -qO- -T 5 http://web:8080; echo "exit=$?"

kubectl delete namespace np-test
```

**Check:**
- **Probe A** prints `<title>Welcome to nginx!</title>`.
- **Probe B** times out and prints a non-zero `exit=`.
- **If the `web` Pod is stuck in `ImagePullBackOff`,** Cloud NAT isn't working. See [Troubleshooting](#16-troubleshooting).

### 9.4 Serve the chart through GKE Gateway

The chart's default edge mode is `gateway`: it renders `HTTPRoute`s and expects a Gateway you run.

1. **The Gateway.** Create one with the GatewayClass `gke-l7-global-external-managed` (9.2), in its own
   namespace, with the listener certificate you manage (the chart renders no TLS in this mode). The
   listener must admit the app namespace in `allowedRoutes.namespaces`: the default (`Same`) admits
   only the Gateway's own namespace, and the chart's HTTPRoutes stay unattached. A minimal one, which
   admits namespaces with the chart's `tier: app-ns` label:

   ```yaml
   apiVersion: gateway.networking.k8s.io/v1
   kind: Gateway
   metadata:
     name: edge
     namespace: gateway-system
   spec:
     gatewayClassName: gke-l7-global-external-managed
     listeners:
       - name: https
         protocol: HTTPS
         port: 443
         tls:
           mode: Terminate
           certificateRefs: [{name: edge-tls}]   # a TLS Secret in gateway-system
         allowedRoutes:
           namespaces:
             from: Selector
             selector:
               matchLabels: {tier: app-ns}
   ```
2. **Point the chart at it.** Set `edge.gateway.parentRefs` to that Gateway's name and namespace; the
   placeholder `your-gateway` must not reach production.
3. **Turn on the GKE policies.** Set `edge.gateway.gke.enabled: true`. GKE does not read readiness
   probes and health-checks `/` by default; the backend has no `/`, so without the chart's
   HealthCheckPolicy the load balancer marks it unhealthy and serves nothing. The same switch adds the
   GCPBackendPolicy (timeout and access log) and admits Google's load balancer ranges in the
   NetworkPolicy.
4. **Cloud Armor (optional).** Create a security policy with a rate-based rule (it limits every path,
   the frontend included) and name it in `edge.gateway.gke.securityPolicy`.

What each setting renders: [Infrastructure, GKE Gateway policies](./infrastructure.md#gke-gateway-policies).
Google's references: [Gateway resources using Policies](https://cloud.google.com/kubernetes-engine/docs/how-to/configure-gateway-resources),
[Cloud Armor rate limiting](https://cloud.google.com/armor/docs/rate-limiting-overview).

## 10. The image registry

> **Owned by lodestar-infra (L1.2).** You can do this by hand, or leave it to lodestar-infra. If you do it by hand, you'll need `tofu import` or a rebuild later. **Optional, and not wired up yet:** the template pushes to GHCR. No workflow reads `vars.IMAGE_REGISTRY`, and the provider condition of 12.2 admits no image-pushing workflow, so nothing can push to Artifact Registry today. Skip this section unless you plan ahead.

The template's default registry is GHCR (`ghcr.io`), which needs no setup here. Create Artifact Registry only if you choose it instead. It then sits in the same project and region as the cluster.
- **Pushing:** CI pushes with its keyless identity.
- **Pulling:** the nodes pull through Private Google Access, with no pull secrets. This holds for Artifact Registry only. Private GHCR images need an image pull secret in each app namespace ([13.4](#134-an-image-pull-secret-for-private-ghcr-images)).
- **Immutable tags:** a tag can never be moved to another image. A deployed tag always means the same bytes.

```bash
gcloud artifacts repositories create "$AR_REPO" --repository-format=docker \
  --location="$REGION" --immutable-tags \
  --description="Container images for apps-dev"

gcloud artifacts repositories add-iam-policy-binding "$AR_REPO" --location="$REGION" \
  --member="serviceAccount:$NODE_SA" --role="roles/artifactregistry.reader"
```

### Cleanup policy

This keeps storage costs flat. Tagged images can't be deleted while tags are immutable, so the policy removes only untagged leftovers older than 30 days.

```bash
cat > /tmp/ar-cleanup.json <<'EOF'
[
  { "name": "delete-untagged-after-30d", "action": { "type": "Delete" },
    "condition": { "tagState": "untagged", "olderThan": "30d" } },
  { "name": "keep-10-most-recent", "action": { "type": "Keep" },
    "mostRecentVersions": { "keepCount": 10 } }
]
EOF
gcloud artifacts repositories set-cleanup-policies "$AR_REPO" --location="$REGION" \
  --policy=/tmp/ar-cleanup.json --dry-run      # first, see what it would do
gcloud artifacts repositories set-cleanup-policies "$AR_REPO" --location="$REGION" \
  --policy=/tmp/ar-cleanup.json --no-dry-run
```

Policies take effect within about a day.

**Check:**

```bash
gcloud artifacts repositories describe "$AR_REPO" --location="$REGION" \
  --format='value(format,dockerConfig.immutableTags)'
# → DOCKER  True
echo "Registry path: $REGION-docker.pkg.dev/$PROJECT_ID/$AR_REPO"
```

## 11. The GitHub repository

> **Do now** for 11.1, the empty repository, the Actions settings and the environments. **Planned change (the settings script applies them after the first push):** the rulesets, and later `scripts/github-settings.sh` and `wiki/github-repository-settings.md`, replace the hand steps of 11.2.

### 11.1 Public or private?

The template no longer contains company identifiers, so publishing it is now possible. Before you do, make sure you're entitled to publish its code. Here is what you get on a **free** GitHub account:

| Feature | Public repo (any plan) | Private repo |
|---|---|---|
| Environments, environment secrets and variables, deployment branch and tag policies | yes | Free: no. GitHub Pro (personal) or Team (organisation): yes |
| Required reviewers and wait timers (the production approval gate) | yes | **GitHub Enterprise only** |
| Code scanning (the template's SARIF results as alerts) | yes | needs paid GitHub Code Security |
| Secret scanning and push protection | yes | limited |
| Dependency review on pull requests | yes | needs paid GitHub Code Security |

Sources: [Managing environments for deployment](https://docs.github.com/en/actions/managing-workflow-runs-and-deployments/managing-deployments/managing-environments-for-deployment) ("Users with GitHub Free plans can only configure environments for public repositories"; Team and Pro can configure them for private ones) and [Deployments and environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments) ("If you are on a GitHub Free, GitHub Pro, or GitHub Team plan, required reviewers are only available for public repositories").

**Recommendation: public.** The template's repository is public, so it gets the production approval gate and every scan at no cost. A **private** copy on Pro or Team can run the deploys, with the branch policies, but gets **no approval gate**: that needs Enterprise or a public repository. On Free, a private copy can't use environments at all, so the deploy workflows can't run safely; the pipeline still runs every scan and blocks on failures, with results kept as workflow artifacts. There is no manual fallback.

### 11.2 Create and harden the repository

1. Create the repository `$GH_REPO`, public or private per 11.1, **empty**: no README, no licence, no `.gitignore`. Then the first push of the code is a plain push. If the repository already has GitHub's initial commit, the first push needs one force-push, `git push --force origin main`, done once and **before any ruleset exists**.
2. Under **Settings → Actions → General:**
   - **Actions permissions:**
     - allow GitHub-made actions and actions by Marketplace verified creators, or only selected actions;
     - tick **Require actions to be pinned to a full-length commit SHA**.
   - **Workflow permissions:**
     - choose **Read repository contents and packages permissions**, so each workflow asks for anything more itself;
     - untick **Allow GitHub Actions to create and approve pull requests**.
3. **Rulesets: not now.** The `main` branch ruleset and a `v*` tag ruleset are applied by the `scripts/github-settings.sh` script *after* the first push. A hand-made `main` ruleset (a pull request required, no bypass) would refuse that first push, and a pull request isn't possible because the template history and GitHub's own commit are unrelated. The settings script also owns the required status checks (an aggregate gate job, so the names match the jobs).
4. Under **Settings → Environments**, create `dev`, `qa` and `production`. This needs a public repo, or a private one on Pro or Team (11.1). For each, under **Deployment branches and tags**, choose **Selected branches and tags** and add the rules. GitHub's dialog defaults to the type **Branch**, so pick the type for each rule:
   - **`dev` and `qa`:** a **branch** rule `main` and a **tag** rule `v*`;
   - **`production`:** a **tag** rule `v*` only; also add yourself as a required reviewer (public repo or Enterprise, see 11.1).

   Production operations (`deploy-ops`) are dispatched from a `v*` tag ref. The settings script adds a fourth environment, `release`, for the release GitHub App key.
5. **Public repos:** under **Settings → Code security**, turn on secret scanning, push protection and Dependabot alerts.

**Check:** opening **Settings → Actions → General** shows the SHA-pinning requirement ticked and read-only workflow permissions.

## 12. Keyless access for GitHub Actions

> **Do now** for 12.1. **Owned by lodestar-infra (L1.2)** for 12.2 and 12.3 (the pool, the provider and the deployers): you can do it by hand, or leave it to lodestar-infra. If you do it by hand, you'll need `tofu import` or a rebuild later. A single `gh-deployer` made by hand must be replaced, not imported, and a deleted pool's name stays reserved for 30 days.

GitHub signs a short-lived token for each workflow run. Google accepts it only if all three of these hold:
- the repository owner's **numeric ID** matches, which survives renames and can't be reused by someone else;
- the **repository ID** matches, so a deleted and re-created repository of the same name is a stranger;
- the running workflow file is `deploy.yml` or `deploy-ops.yml` of this repository, on `main` or a `v*` tag (`job_workflow_ref`).

The workflow then acts as the deployer service account of its own GitHub environment, and no other (12.3). No key file is created.

### 12.1 Look up your numeric owner and repository IDs

```bash
export GH_OWNER_ID="$(curl -fsSL "https://api.github.com/users/$GH_OWNER" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
echo "export GH_OWNER_ID=\"$GH_OWNER_ID\"" >> ~/gke-setup.env
echo "$GH_OWNER_ID"

# The repository must exist (11.2); its ID doesn't change when it is renamed or moved.
# GitHub CLI first (works for private repositories); else the public API.
GH_REPO_ID="$(gh api "repos/$GH_REPO" --jq .id 2>/dev/null \
  || curl -fsS "https://api.github.com/repos/$GH_REPO" 2>/dev/null \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' 2>/dev/null)"
export GH_REPO_ID
[[ "$GH_REPO_ID" =~ ^[0-9]+$ ]] || echo "GH_REPO_ID is empty or not a number: don't continue"
echo "$GH_REPO_ID"
```

**Check:** both IDs print a number. Only then save the repository ID:

```bash
echo "export GH_REPO_ID=\"$GH_REPO_ID\"" >> ~/gke-setup.env
```

The owner endpoint works for user accounts and organisations alike.

### 12.2 The identity pool and the GitHub provider

```bash
CONDITION="assertion.repository_owner_id=='$GH_OWNER_ID' && assertion.repository_id=='$GH_REPO_ID' && assertion.job_workflow_ref.matches('^$GH_REPO/\\\\.github/workflows/(deploy|deploy-ops)\\\\.yml@refs/(heads/main|tags/v.*)\$')"

gcloud iam workload-identity-pools create github \
  --location=global --display-name="GitHub Actions"

gcloud iam workload-identity-pools providers create-oidc github-oidc \
  --location=global --workload-identity-pool=github \
  --display-name="GitHub OIDC" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_owner_id=assertion.repository_owner_id,attribute.repository_id=assertion.repository_id,attribute.job_workflow_ref=assertion.job_workflow_ref,attribute.ref=assertion.ref" \
  --attribute-condition="$CONDITION"
```

The condition, as Google stores it, reads (the regex is a CEL string, so each backslash is doubled):

```text
assertion.repository_owner_id=='<OWNER_ID>' && assertion.repository_id=='<REPO_ID>' && assertion.job_workflow_ref.matches('^<owner>/<repo>/\\.github/workflows/(deploy|deploy-ops)\\.yml@refs/(heads/main|tags/v.*)$')
```

Only those two workflow files can ever get a token: no other workflow, and no workflow on another branch, even in your repository. The `check` operation of `deploy-ops` ([13.3](#133-the-first-check-deploy-ops-check)) is part of `deploy-ops.yml`, so it needs no extra workflow to be allowed.

**Check:**

```bash
gcloud iam workload-identity-pools providers describe github-oidc \
  --location=global --workload-identity-pool=github \
  --format='value(state,attributeCondition)'
# → ACTIVE  <the condition>

# The laptop-side proof (a "Do now" step, no GitHub run needed): the stored condition equals the one above.
actual="$(gcloud iam workload-identity-pools providers describe github-oidc \
  --location=global --workload-identity-pool=github --format='value(attributeCondition)')"
[[ "$actual" == "$CONDITION" ]] && echo "condition matches" || echo "DIFFERENT: compare by eye with the block above"
# → condition matches   (set CONDITION again, as in the create step, if this is a new terminal)
```

The end-to-end proof, a real token from GitHub, comes only after the first push ([13.3](#133-the-first-check-deploy-ops-check)).

### 12.3 One deployer service account per environment

Each GitHub environment gets its own service account. A job of environment `dev` can then never act as the production deployer, because the subject Google sees contains the environment name.

`roles/container.clusterViewer` includes the permission `container.clusters.connect`, which the DNS-based endpoint needs (and which `get-gke-credentials` with `use_dns_based_endpoint` requires at minimum). Confirm it first, before you grant the role (the command prints the permission):

```bash
gcloud iam roles describe roles/container.clusterViewer --format='value(includedPermissions)' | tr ';' '\n' | grep container.clusters.connect
```

Then create the accounts and grant:

```bash
for env in dev qa production; do
  sa="gh-deployer-$env"
  gcloud iam service-accounts create "$sa" --display-name="GitHub Actions deployer ($env)"

  # Only the job of this one environment, in this one repository, may act as it:
  gcloud iam service-accounts add-iam-policy-binding "$sa@$PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/iam.workloadIdentityUser" \
    --member="principal://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/subject/repo:$GH_REPO:environment:$env"

  # Reach the cluster's DNS-based endpoint. This is not the right to deploy:
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --condition=None \
    --member="serviceAccount:$sa@$PROJECT_ID.iam.gserviceaccount.com" --role="roles/container.clusterViewer"
done
```

**Never give a deployer `roles/container.developer` or `roles/container.viewer`.** Through IAM they grant Kubernetes rights in every namespace, which breaks the per-namespace RBAC design below.

**Artifact Registry is not wired up yet.** No workflow pushes to it, and the provider condition admits no image-pushing workflow, so the deployers get no `artifactregistry.writer`. If you later wire it up, that grant belongs to the workflow that builds, with its own condition.

**Don't create a key for any of these service accounts, ever.** If a tutorial tells you to download a JSON key, it's describing the old way.

**Deploy rights are Kubernetes RBAC, delivered by the deploy bootstrap.** The bootstrap creates, in each environment's app namespace, a `Role` and a `RoleBinding` for the deployer's service account email (the Kubernetes user name). For reference, the Role holds:
- `get`, `list` and `watch` on pods, services, deployments, replicasets, jobs and events;
- `get` on `pods/log`;
- `create` on `pods/exec` (the verify check) and `pods/portforward` (the smoke test);
- Helm's verbs on secrets, configmaps and the chart's other kinds, in that namespace only.

Don't write these by hand.

**Check:** until the bootstrap exists, each deployer can do nothing in the cluster. Run the check as an admin, once per environment:

```bash
for env in dev qa production; do
  echo "$env: $(kubectl auth can-i create deployments --namespace default --as="gh-deployer-$env@$PROJECT_ID.iam.gserviceaccount.com")"
done
# → no   (three times)
```

### 12.4 If you followed an earlier version of this guide

The earlier version created a provider with only the owner and repository-name condition, and one `gh-deployer` bound to the whole repository. Bring it up to date, in this order:

```bash
# 1. The provider: new mappings and the three-part condition (CONDITION as in 12.2)
gcloud iam workload-identity-pools providers update-oidc github-oidc \
  --location=global --workload-identity-pool=github \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_owner_id=assertion.repository_owner_id,attribute.repository_id=assertion.repository_id,attribute.job_workflow_ref=assertion.job_workflow_ref,attribute.ref=assertion.ref" \
  --attribute-condition="$CONDITION"

# 2. The old deployer: remove its repository-wide binding, then the account
OLD="gh-deployer@$PROJECT_ID.iam.gserviceaccount.com"
gcloud iam service-accounts remove-iam-policy-binding "$OLD" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/attribute.repository/$GH_REPO"
# List every project role still bound to the old account, then remove each one (none may dangle):
gcloud projects get-iam-policy "$PROJECT_ID" --flatten=bindings \
  --filter="bindings.members:serviceAccount:$OLD" --format='value(bindings.role)'
gcloud projects remove-iam-policy-binding "$PROJECT_ID" --condition=None \
  --member="serviceAccount:$OLD" --role="roles/container.clusterViewer"
# repeat the remove for any other role the list shows; if it had artifactregistry.writer,
# remove that binding on the repository first (gcloud artifacts repositories remove-iam-policy-binding)
gcloud iam service-accounts delete "$OLD" --quiet

# 3. The cluster: add the DNS-based endpoint (it takes a few minutes)
gcloud container clusters update "$CLUSTER" --location="$ZONE" --enable-dns-access
```

Then create the three new deployers (12.3) and update the environment variables (13.1).

## 13. The end-to-end check from GitHub

> **Planned change:** the script sets the variables of 13.1; until then set them by hand. **After the first push:** the `check` run of 13.3. **Planned changes:** the namespaces, the RBAC and the pull secret.

This proves the identity chain, from GitHub to the cluster:
- GitHub can log in to Google with no secret;
- it can reach the cluster through the DNS-based endpoint;
- it shows what the deployer may do in the namespace.

### 13.1 Variables and secrets

The one table of every variable and secret, with its scope, is in [Platform Setup: variables and secrets](./platform-setup.md#variables-and-secrets). It is the single source; this guide doesn't repeat it.

For the cluster, set these in GitHub, **per environment** (**Settings → Environments → `<env>` → Environment variables**):
- `GCP_WIF_PROVIDER`: the same value in all three;
- `GCP_DEPLOYER_SA`: `gh-deployer-<env>@<PROJECT_ID>.iam.gserviceaccount.com`, **different** in each;
- `GCP_PROJECT_ID`, `GKE_CLUSTER` and `GKE_LOCATION`: set once as repository variables, and override per environment only if an environment uses another cluster.

None of them is secret. To print the values from your terminal:

```bash
source ~/gke-setup.env
printf '%s\n' "GCP_PROJECT_ID=$PROJECT_ID" "GKE_CLUSTER=$CLUSTER" "GKE_LOCATION=$ZONE" \
 "GCP_WIF_PROVIDER=projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/providers/github-oidc"
for env in dev qa production; do echo "GCP_DEPLOYER_SA ($env)=gh-deployer-$env@$PROJECT_ID.iam.gserviceaccount.com"; done
```

Also set `SERVICE_NAME` (repository variable) to your service's name. It decides the Helm release name, and the namespaces are `<SERVICE_NAME>-<env>`: `<SERVICE_NAME>-dev`, `<SERVICE_NAME>-qa` and `<SERVICE_NAME>-production`. A database namespace `<SERVICE_NAME>-<env>-db` exists only when the chart's PostgreSQL or the external database is enabled. The deploy and ops workflows compute the namespace the same way, so the value must be the same everywhere.

### 13.2 The namespaces and the deployer RBAC come from the bootstrap

**Don't create the namespaces by hand.** Until the bootstrap exists, the chart renders its own `Namespace` objects while the deploy doesn't pass `--create-namespace`, and a namespace made by hand lacks Helm's ownership metadata, which breaks `helm upgrade --install`. The bootstrap creates the namespaces with Pod Security labels, a quota and a LimitRange, plus the deployer RBAC of 12.3, and sets `namespaces.create: false` in the chart. Deploys stay off (`DEPLOY_ENABLED` unset) until the namespace and RBAC bootstrap exists and the platform rollout starts.

### 13.3 The first check: `deploy-ops` `check`

> **After the first push.** A manually started workflow appears only once it is on the default branch, and the code isn't pushed yet. Until then, the laptop-side proof of 12.2 is the strongest check you can run.

There is no separate check workflow. The `check` operation of `deploy-ops.yml` does it: it checks the environment name, logs in through Workload Identity Federation, gets the cluster credentials through the DNS-based endpoint, then runs `kubectl version` and `kubectl auth can-i --list -n <SERVICE_NAME>-<env>`. It's read-only, needs no confirmation, and holds the same lock as the deploys.

Run it from **Actions → Deploy operations → Run workflow**, with `operation: check` and the environment. For `production`, run it from a `v*` tag ref (the environment's branch policy refuses `main`); its first `check` therefore waits for the first `v*` tag that contains the operation, with `DEPLOY_ENABLED` still unset.

**Check:** the run is green, and its log shows:
- a server version from `kubectl version`;
- a short `can-i --list` for the namespace: the default rights every authenticated user has, possibly with a "may be incomplete" warning. Before the namespace and RBAC bootstrap the deployer has no rights of its own in that namespace, so a short list is expected, and the run is still green. A server version plus that list prove the identity chain.

If a step fails, see [Troubleshooting](#16-troubleshooting).

**Order:** set `DEPLOY_ENABLED=true` only after the first successful `check` of each environment you will deploy to, and not before the platform rollout.

### 13.4 An image pull secret for private GHCR images

> **Planned changes:** do this after the namespaces exist.

If your images in GHCR are private, each app namespace needs a pull secret. Create a read-only credential yourself: a token with the `read:packages` scope only, owned by an account that can read the package. Never reuse a write token. Then, per namespace:

```bash
export GH_USER="your-github-user" SERVICE_NAME="your-service-name"
read -rs GHCR_PULL_TOKEN && export GHCR_PULL_TOKEN   # paste the token; it isn't echoed
for env in dev qa production; do
  jq -n '{auths:{"ghcr.io":{auth:((env.GH_USER+":"+env.GHCR_PULL_TOKEN)|@base64)}}}' \
    | kubectl -n "$SERVICE_NAME-$env" create secret generic registry-credentials \
        --type=kubernetes.io/dockerconfigjson --from-file=.dockerconfigjson=/dev/stdin
done
unset GHCR_PULL_TOKEN
```

Then list the secret in the values of each environment: `global.imagePullSecrets: [registry-credentials]`. The production values already do; `values.dev.yaml` and `values.qa.yaml` don't, so add it there. The token never goes on a command line or into Git.

## 14. Costs, pausing and teardown

> **Do now.**

### Approximate monthly cost

These are list prices from 2026 and change over time. The [GKE pricing](https://cloud.google.com/kubernetes-engine/pricing) page is the source of truth.

| Item | Approximate cost |
|---|---|
| Cluster management fee | $0 for one zonal cluster: GKE's free tier credit covers it |
| 2 × e2-standard-2 nodes | about $95 on demand, about $30 to $40 on spot |
| 2 × 50 GB balanced disks | about $10 |
| Cloud NAT | about $1 a month, plus about $0.045 per GB processed |
| Artifact Registry | cents, for a few GB of images |
| Load balancer, only if you choose the Gateway option over the Cloudflare Tunnel | about $18 to $20 |

### Pause and resume

```bash
gcloud container clusters resize "$CLUSTER" --location="$ZONE" --num-nodes=0 --quiet   # pause the nodes
gcloud container clusters resize "$CLUSTER" --location="$ZONE" --num-nodes=2 --quiet   # resume
```

While paused you pay only for disks that still exist, and for the load balancer if one is in use.

### Teardown

Teardown deletes everything this guide created. Run it in this order:

```bash
gcloud container clusters delete "$CLUSTER" --location="$ZONE" --quiet
gcloud artifacts repositories delete "$AR_REPO" --location="$REGION" --quiet
gcloud compute routers nats delete apps-nat --router=apps-router --region="$REGION" --quiet
gcloud compute routers delete apps-router --region="$REGION" --quiet
gcloud compute networks subnets delete "$SUBNET" --region="$REGION" --quiet
gcloud compute networks delete "$NETWORK" --quiet
gcloud iam workload-identity-pools delete github --location=global --quiet   # kept for 30 days: the name stays reserved until it is purged
for env in dev qa production; do gcloud iam service-accounts delete "gh-deployer-$env@$PROJECT_ID.iam.gserviceaccount.com" --quiet; done
gcloud iam service-accounts delete "$NODE_SA" --quiet
```

Remove the project-level role bindings too, or simply delete the whole project.

## 15. Final checklist and the values to hand over

> **Do now.**

| # | Check | Command or place | Expected |
|---|---|---|---|
| 1 | Budget alert exists | Billing → Budgets | listed |
| 2 | Seven APIs enabled | step 5 check | `7` |
| 3 | Subnet has Private Google Access and secondary ranges | step 6 check | `True`, `pods`, `services` |
| 4 | Node SA has only the node roles | step 7 check | no editor or owner |
| 5 | Cluster settings | step 8 check | RUNNING, REGULAR, ADVANCED_DATAPATH, gke-nodes |
| 6 | Nodes are private | `kubectl get nodes -o wide` | EXTERNAL-IP `<none>` |
| 7 | Gateway classes present | `kubectl get gatewayclass` | `gke-l7-*` |
| 8 | NAT and NetworkPolicy | step 9.3 | probe A ok, probe B blocked |
| 9 | *Optional (Artifact Registry only, not wired up yet):* registry is immutable | step 10 check | `DOCKER True` |
| 10 | Repository hardened | Settings → Actions | SHA pinning, read-only token |
| 11 | Provider is active, with the three-part condition | step 12.2 check | `ACTIVE` |
| 12 | CI can't deploy yet, for each of the three deployers | step 12.3 check | `no`, three times |
| 13 | End-to-end from GitHub, **after the first push** | step 13.3 | green `deploy-ops` `check` |

When rows 1 to 12 pass (leaving out optional row 9 if you skipped section 10), you're ready for the first push: set the values of [13.1](#131-variables-and-secrets) in GitHub (none is secret), push, then run row 13.

## 16. Troubleshooting

> **Do now.**

| Symptom | Likely cause | Fix |
|---|---|---|
| `gke-gcloud-auth-plugin … not found` | the plugin isn't installed | install it (2.1), then run `get-credentials` again |
| A gcloud flag is "unrecognized" | gcloud is too old | `gcloud components update`, then rerun 8.1 |
| Cluster create asks for `--master-ipv4-cidr` | the older private-cluster model | add `--master-ipv4-cidr=172.16.0.32/28` |
| Cluster create fails on quota (`CPUS`, `IN_USE_ADDRESSES`, `SSD_TOTAL_GB`) | new projects have low quotas | IAM & Admin → Quotas: request more, or use fewer or smaller nodes |
| `ImagePullBackOff` for Docker Hub images | no outbound route: NAT is missing or covers the wrong region | recheck step 6 and that NAT and cluster are in the same region |
| `ImagePullBackOff` for private `ghcr.io` images | no pull secret in the namespace | [13.4](#134-an-image-pull-secret-for-private-ghcr-images) |
| `ImagePullBackOff` for `…pkg.dev` images | the node SA can't read the registry, or the path is wrong | recheck the reader binding in step 10, and the `REGION-docker.pkg.dev/PROJECT/apps/...` path |
| Actions: `The given credential is rejected by the attribute condition` | wrong owner ID, repository ID or repository name (`GH_REPO`, which is case-sensitive), or the job runs from a workflow file or ref the condition doesn't allow (for example a branch other than `main`, or another workflow) | compare with `providers describe`, and fix with `providers update-oidc --attribute-condition=…` |
| Actions: `Permission 'iam.serviceAccounts.getAccessToken' denied` | the `workloadIdentityUser` binding is missing, or names a different repository or environment | recheck step 12.3; the subject (`repo:<owner>/<repo>:environment:<env>`) must match exactly |
| Actions: `Unable to get ACTIONS_ID_TOKEN_REQUEST_URL` | the workflow lacks `id-token: write` | the workflows already declare it; if you changed one, restore `id-token: write` on that job |
| Actions: "must be pinned to a full-length commit SHA" | the repo setting from 11.2 is doing its job | use the SHA form, as every workflow here does |
| `kubectl` hangs or times out | wrong kubeconfig, or the cluster is resized to 0 | `source ~/gke-setup.env`, then check `kubectl config current-context` and the node count |

## 17. What comes next

> **Planned changes.** Everything below is not implemented yet.

These come with the GitHub pipeline work, not in this guide:

- **Namespaces and deploy rights:** the bootstrap creates the namespaces (with Pod Security labels, quota and LimitRange) and one Role per app namespace for each deployer; the chart stops creating namespaces.
- **Settings script and release App:** The settings script replaces the hand steps of 11.2 and adds the `release` environment and its GitHub App.
- **Domain, TLS and public traffic:** through Cloudflare. See [Cloudflare Setup](./cloudflare-setup.md): a Tunnel by default, so the cluster has no public load balancer. The alternative is a `Gateway` with a Google-managed certificate.
- **Signed images only:** keyless cosign signing and build provenance in CI, then admission enforcement: a Kyverno policy or Binary Authorization on GKE, later.
- **Database:** Cloud SQL for PostgreSQL for production, reached privately and with IAM authentication.
- **Closing the public endpoint:** the DNS-based endpoint is already on (8.2). Later, turn the public IP endpoint off. Never allowlist CI addresses on it.
- **The edge mode:** the template's default `edge.mode` is `gateway`; the platform values use `tunnel`.
- **Infrastructure as code:** everything in this guide expressed in OpenTofu or Terraform, with state in a locked GCS bucket, so the cluster can be rebuilt from Git.
- **Backups and recovery:** Backup for GKE, and Cloud SQL point-in-time recovery.
- **Kynosura:** the course site packaged as a Helm chart and deployed to this same cluster.
