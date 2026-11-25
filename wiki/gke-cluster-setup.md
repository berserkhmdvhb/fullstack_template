# GKE cluster setup for the fullstack template

This guide builds the Google Kubernetes Engine (GKE) cluster that the template's GitHub pipeline deploys to. It's steps 1 and 2 of [Platform Setup](./platform-setup.md). It also sets up the keyless link between GitHub Actions and Google Cloud.

Each section starts with a tag (**Do now**, **Planned change**, or **Managed as code**); [How to read this guide](./platform-setup.md#how-to-read-this-guide) explains them. Work through it top to bottom. Every step ends with a **Check** that tells you what you should see. Don't go on to the next step until the check passes.

| | |
|---|---|
| Time | 60 to 90 minutes, about 15 of it waiting for Google Cloud |
| Cost | about $40 to $120 a month while the cluster runs (see [Costs](#15-costs-pausing-and-teardown)) |
| You need | a Google Cloud project with billing, a GitHub account and a Linux or macOS terminal |
| Matches | the GitHub Actions workflows in `.github/workflows/`. Written 2026-11-20; checked against Google's docs on 2026-11-25. Cloud CLIs change, so each step checks your own tools before it relies on them. |

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
14. [The app's secrets: External Secrets with Secret Manager](#14-the-apps-secrets-external-secrets-with-secret-manager)
15. [Costs, pausing and teardown](#15-costs-pausing-and-teardown)
16. [Final checklist and the values to hand over](#16-final-checklist-and-the-values-to-hand-over)
17. [Troubleshooting](#17-troubleshooting)
18. [What comes next](#18-what-comes-next)

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
| Cluster name | `apps-dev` | It hosts the template's dev environment, and later your other services. |
| GitHub repository | `<you>/fullstack-template` | Created in [step 11](#11-the-github-repository). |
| Repository visibility | **public** (recommended) | See [11.1](#111-public-or-private). It changes which GitHub security features you get for free. |
| One cluster for every environment | yes: `dev`, `qa` and `production` are namespaces of `apps-dev` | The cheapest setup: one zonal cluster, whose management fee the free tier covers. The trade-offs: one blast radius; a zonal control plane is briefly unavailable while it upgrades (workloads keep running); a zone outage stops all three environments. For a production cluster of its own, repeat 6 to 9 for it and set `GKE_CLUSTER` and `GKE_LOCATION` on the `production` environment ([13.1](#131-variables-and-secrets)); a second cluster pays the management fee, about $73 a month. |
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

A budget alerts you; it doesn't stop spending. [Section 15](#15-costs-pausing-and-teardown) shows how to pause the cluster.

**Check:**

```bash
gcloud billing projects describe "$PROJECT_ID" --format='value(billingEnabled)'   # → True
```

Also confirm the budget appears in the console list.

## 5. Enable the APIs

> **Do now.** Enabling the APIs stays a manual step: it comes before any infrastructure code can create resources.

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

> **Managed as code.** You can do this by hand, or leave it to your infrastructure-as-code repository. If you do it by hand, bringing it under code later needs an import (`tofu import`) or a rebuild.

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

> **Managed as code.** You can do this by hand, or leave it to your infrastructure-as-code repository. If you do it by hand, bringing it under code later needs an import (`tofu import`) or a rebuild.

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

> **Managed as code.** You can do this by hand, or leave it to your infrastructure-as-code repository. If you do it by hand, bringing it under code later needs an import (`tofu import`) or a rebuild. The cluster is the costliest thing to import; for dev, a rebuild is simpler.

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
  --maintenance-window-start=2026-11-21T00:00:00Z \
  --maintenance-window-end=2026-11-21T08:00:00Z \
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
| `--maintenance-window-*` | Upgrades happen only from 00:00 to 08:00 UTC on Saturday and Sunday. GKE needs at least 48 hours of maintenance time in any 32 days, counted in blocks of 4 hours or more, and two 8-hour days a week give at least 64. Choose your own hours, but keep that total. |

Both flags, `--enable-dns-access` and `--no-enable-ip-access`, are described in GKE's [network isolation how-to](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/latest/network-isolation). The public IP endpoint stays on for now: closing it with `--enable-private-endpoint` is a step of the platform rollout ([18.2](#182-close-the-public-ip-endpoint-at-the-platform-rollout)), and you never put a CIDR allowlist on it for CI.

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
# the DNS-based endpoint, as CI uses; it keeps working once the public IP endpoint is closed (18.2)
gcloud container clusters get-credentials "$CLUSTER" --location="$ZONE" --dns-endpoint
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
- **If the `web` Pod is stuck in `ImagePullBackOff`,** Cloud NAT isn't working. See [Troubleshooting](#17-troubleshooting).

### 9.4 Choose the edge mode

Decide how traffic reaches the chart. The two modes:

- **`tunnel`, this platform's choice.** Cloudflare Tunnel ([Cloudflare §8](./cloudflare-setup.md#8-cloudflare-tunnel-into-gke)): no load balancer and no public origin. Set `edge.mode: tunnel` in `charts/your-service-name/values.dev.yaml`, `values.qa.yaml` and `values.production.yaml`, with your hostnames (`edge.hosts`; [Infrastructure, Edge](./infrastructure.md#edge)). The chart's default is `gateway`, and a deploy that still names the placeholder Gateway is refused. Nothing else to do on GKE now: skip the rest of 9.4. **Check:** `grep -H 'mode: tunnel' charts/your-service-name/values.{dev,qa,production}.yaml` prints three lines. (Pull request previews still use a Gateway, an internal one: [12.5](#125-optional-pull-request-previews).)
- **`gateway`.** A public GKE Gateway: an external load balancer (about $18 to $20 a month, see [15](#15-costs-pausing-and-teardown)), a listener certificate you manage, and Cloudflare in front as a proxied DNS record ([Cloudflare §1](./cloudflare-setup.md#1-what-you-will-build)). Do the steps below at the platform rollout, after the bootstrap ([13.2](#132-the-namespaces-and-the-deployer-rbac-come-from-the-bootstrap)) has created the namespaces; the chart isn't deployed before then.

The `gateway` steps:

The chart's `gateway` mode renders `HTTPRoute`s and expects a Gateway you run.

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

> **Managed as code.** You can do this by hand, or leave it to your infrastructure-as-code repository. If you do it by hand, bringing it under code later needs an import (`tofu import`) or a rebuild. **Optional, and not wired up yet:** the template pushes to GHCR. No workflow reads `vars.IMAGE_REGISTRY`, and the provider condition of 12.2 admits no image-pushing workflow, so nothing can push to Artifact Registry today. Skip this section unless you plan ahead.

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

> **Do now** for 11.1, the empty repository, the Actions settings and the environments. **After the first push:** [`scripts/github-settings.sh`](../scripts/github-settings.sh) applies the rulesets and applies the rest of 11.2 as code (the Actions settings, the environments and their rules, secret scanning, push protection and Dependabot alerts; [GitHub repository settings](./github-repository-settings.md)).

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

> **Do now** for 12.1. **Managed as code** for 12.2 and 12.3 (the pool, the provider and the deployers): you can do it by hand, or leave it to your infrastructure-as-code repository. If you do it by hand, bringing it under code later needs an import (`tofu import`) or a rebuild. A single `gh-deployer` made by hand must be replaced, not imported, and a deleted pool's name stays reserved for 30 days.

GitHub signs a short-lived token for each workflow run. Google accepts it only if all three of these hold:
- the repository owner's **numeric ID** matches, which survives renames and can't be reused by someone else;
- the **repository ID** matches, so a deleted and re-created repository of the same name is a stranger;
- the running workflow file is `deploy.yml` or `deploy-ops.yml` of this repository, on `main` or a `v*` tag (`job_workflow_ref`).

The workflow then acts as the deployer service account of its own GitHub environment, and no other (12.3). No key file is created. The optional live-drift check reads the cluster through providers and accounts of its own, which can't deploy (12.6).

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

Only those two workflow files can ever get a token from this provider: no other workflow, and no workflow on another branch, even in your repository. The optional pull request previews (`preview.yml`) use a second provider with a condition of their own ([CI/CD, Pull request previews](./ci-cd.md#pull-request-previews)). The `check` operation of `deploy-ops` ([13.3](#133-the-first-check-deploy-ops-check)) is part of `deploy-ops.yml`, so it needs no extra workflow to be allowed.

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

The DNS-based endpoint needs the permission `container.clusters.connect` (and `get-gke-credentials` with `use_dns_based_endpoint` requires it at minimum). `roles/container.clusterViewer` has included it, but Google's docs now also name `roles/container.developer` and `roles/container.viewer` as holders, and this guide forbids both. So check the role first, and fall back to a custom role if Google changed it:

```bash
if gcloud iam roles describe roles/container.clusterViewer --format='value(includedPermissions)' \
     | tr ';' '\n' | grep -qx container.clusters.connect; then
  export DEPLOYER_CLUSTER_ROLE="roles/container.clusterViewer"
else
  # Google changed the role: create one with exactly the two permissions a deployer needs to connect.
  gcloud iam roles create gkeDnsConnect --project="$PROJECT_ID" --title="GKE DNS endpoint connect" \
    --permissions=container.clusters.get,container.clusters.connect --stage=GA
  export DEPLOYER_CLUSTER_ROLE="projects/$PROJECT_ID/roles/gkeDnsConnect"
fi
echo "export DEPLOYER_CLUSTER_ROLE=\"$DEPLOYER_CLUSTER_ROLE\"" >> ~/gke-setup.env
echo "$DEPLOYER_CLUSTER_ROLE"
```

**Check:** the last line prints `roles/container.clusterViewer` or `projects/<PROJECT_ID>/roles/gkeDnsConnect`.

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
    --member="serviceAccount:$sa@$PROJECT_ID.iam.gserviceaccount.com" --role="$DEPLOYER_CLUSTER_ROLE"
done
```

**Never give a deployer `roles/container.developer` or `roles/container.viewer`.** Through IAM they grant Kubernetes rights in every namespace, which breaks the per-namespace RBAC design below.

**Artifact Registry is not wired up yet.** No workflow pushes to it, and the provider condition admits no image-pushing workflow, so the deployers get no `artifactregistry.writer`. If you later wire it up, that grant belongs to the workflow that builds, with its own condition.

**Don't create a key for any of these service accounts, ever.** If a tutorial tells you to download a JSON key, it's describing the old way.

**Deploy rights are Kubernetes RBAC, delivered by the namespace bootstrap.** The bootstrap creates, in each of the environment's namespaces, the `Role` and `RoleBinding` `bootstrap-deployer` for the deployer's service account email (the Kubernetes user name). The Role holds Helm's verbs on exactly the kinds the chart renders there (Helm's release Secrets included), read access to Pods and ReplicaSets, `get` on `pods/log`, and `create` on `pods/portforward` (the verify check and the smoke test); never `pods/exec`, namespaces, quotas or any Role or RoleBinding (the bootstrap applies the app's own). The full list and why: [Infrastructure: namespace bootstrap](./infrastructure.md#namespace-bootstrap-one-time-admin-step).

Don't write these by hand: render them (13.2).

**Check:** until the bootstrap is applied, each deployer can do nothing in the cluster. Run the check as an admin, once per environment:

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

### 12.5 Optional: pull request previews

> **Only if you turn previews on** ([CI/CD, Pull request previews](./ci-cd.md#pull-request-previews)); they're off by default. Do this after [Cloudflare §8.3](./cloudflare-setup.md#83-run-the-connector-in-the-cluster) and the first push (Platform Setup, the optional previews row), not on the first pass through this guide.

Previews need, on Google Cloud, a deployer, a second provider, and an internal Gateway that the Tunnel reaches privately. One wildcard tunnel route carries every preview hostname, and the Gateway routes each one to its preview by host name.

#### 1. The deployer

As in 12.3 with `env=preview`:

```bash
env=preview
sa="gh-deployer-$env"
gcloud iam service-accounts create "$sa" --display-name="GitHub Actions deployer ($env)"
gcloud iam service-accounts add-iam-policy-binding "$sa@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principal://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/subject/repo:$GH_REPO:environment:$env"
gcloud projects add-iam-policy-binding "$PROJECT_ID" --condition=None \
  --member="serviceAccount:$sa@$PROJECT_ID.iam.gserviceaccount.com" --role="$DEPLOYER_CLUSTER_ROLE"
```

**Check:** as in 12.3, `kubectl auth can-i create deployments --namespace default --as="gh-deployer-preview@$PROJECT_ID.iam.gserviceaccount.com"` prints `no`.

#### 2. The provider

The provider `github-oidc-preview`, in the same pool, with the issuer and attribute mapping of 12.2:

```bash
PREVIEW_CONDITION="assertion.repository_owner_id=='$GH_OWNER_ID' && assertion.repository_id=='$GH_REPO_ID' && assertion.environment=='preview' && assertion.job_workflow_ref.matches('^$GH_REPO/\\\\.github/workflows/preview\\\\.yml@refs/(pull/[0-9]+/merge|heads/main)\$')"

gcloud iam workload-identity-pools providers create-oidc github-oidc-preview \
  --location=global --workload-identity-pool=github \
  --display-name="GitHub OIDC (previews)" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_owner_id=assertion.repository_owner_id,attribute.repository_id=assertion.repository_id,attribute.job_workflow_ref=assertion.job_workflow_ref,attribute.ref=assertion.ref" \
  --attribute-condition="$PREVIEW_CONDITION"
```

The condition expands to exactly the one [CI/CD, Pull request previews](./ci-cd.md#pull-request-previews) states, with your owner and repository IDs.

**Check:** the same stored-condition comparison as 12.2, against `$PREVIEW_CONDITION`:

```bash
actual="$(gcloud iam workload-identity-pools providers describe github-oidc-preview \
  --location=global --workload-identity-pool=github --format='value(attributeCondition)')"
[[ "$actual" == "$PREVIEW_CONDITION" ]] && echo "condition matches" || echo "DIFFERENT: compare by eye with the CI/CD page"
# → condition matches
```

Then set these variables on the `preview` GitHub environment (print them, as 13.1 does):

```bash
printf '%s\n' \
  "GCP_WIF_PROVIDER=projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/providers/github-oidc-preview" \
  "GCP_DEPLOYER_SA=gh-deployer-preview@$PROJECT_ID.iam.gserviceaccount.com"
```

#### 3. The proxy-only subnet

The subnet the internal load balancer needs (one per region and network):

```bash
gcloud compute networks subnets create apps-proxy-only --purpose=REGIONAL_MANAGED_PROXY --role=ACTIVE \
  --network="$NETWORK" --region="$REGION" --range=10.129.0.0/23
```

GKE creates the firewall rules for its Gateways itself (on a Shared VPC you write them by hand).

**Check:** `gcloud compute networks subnets describe apps-proxy-only --region="$REGION" --format='value(purpose,role)'` prints `REGIONAL_MANAGED_PROXY  ACTIVE`.

#### 4. The internal Gateway

Do this after the preview bootstrap ([CI/CD, Pull request previews](./ci-cd.md#pull-request-previews), step 1) so the namespaces exist. Create the namespace `gateway-system` first if it doesn't exist.

```bash
kubectl create namespace gateway-system --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f - <<'EOF'
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: preview-internal
  namespace: gateway-system
spec:
  gatewayClassName: gke-l7-rilb
  listeners:
    - name: http
      protocol: HTTP
      port: 80
      allowedRoutes:
        namespaces:
          from: Selector
          selector:
            matchLabels: {tier: preview-app}
EOF
```

The selector is `tier: preview-app`, not `app-ns`: the preview namespaces carry that label (the bootstrap's `appTier` for previews). HTTP is right here: Cloudflare terminates TLS, and the hop from the connector to the Gateway stays inside the VPC, as the Tunnel's hop to a Service does. Then:

```bash
kubectl -n gateway-system wait --for=condition=Programmed gateway/preview-internal --timeout=600s
export PREVIEW_GW_IP="$(kubectl -n gateway-system get gateway preview-internal -o jsonpath='{.status.addresses[0].value}')"
echo "export PREVIEW_GW_IP=\"$PREVIEW_GW_IP\"" >> ~/gke-setup.env
echo "$PREVIEW_GW_IP"   # → a private address; Cloudflare §9.2 routes to it
```

The address lasts as long as the Gateway; if you recreate the Gateway, update the route in [Cloudflare §9.2](./cloudflare-setup.md#92-optional-the-previews-hostnames-access-application-and-service-token). Then set these in the chart's `charts/your-service-name/values.preview.yaml`:

```yaml
edge:
  gateway:
    parentRefs:
      - {name: preview-internal, namespace: gateway-system}
    gke:
      enabled: true
  extraPeers:
    - {ipBlock: {cidr: 10.129.0.0/23}}
```

`gke.enabled` renders the HealthCheckPolicy (GKE doesn't read readiness probes) and admits Google's health-check ranges. `extraPeers` admits the load balancer's proxies, which come from the proxy-only subnet. Finally, allow the connector's egress to the Gateway (the connector's NetworkPolicy of [Cloudflare §8.3](./cloudflare-setup.md#83-run-the-connector-in-the-cluster) allows no private address by default):

```bash
kubectl apply -f - <<EOF
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: cloudflared-to-preview-gateway
  namespace: cloudflared
spec:
  podSelector: {}
  policyTypes: [Egress]
  egress:
    - to: [{ipBlock: {cidr: ${PREVIEW_GW_IP}/32}}]
      ports: [{protocol: TCP, port: 80}]
EOF
```

**Check:** `kubectl -n gateway-system get gateway preview-internal` shows `PROGRAMMED` `True` and a private address, normally from `$SUBNET`'s range (`10.10.0.0/20`), never from the proxy-only subnet; `kubectl -n cloudflared get networkpolicy` lists `cloudflared-to-preview-gateway`.

### 12.6 Optional: the live-drift identity

> **Only if you turn the live-drift check on** ([CI/CD, Live drift](./ci-cd.md#live-drift)); without it the weekly run skips each environment with a summary line and stays green. Do this after the first deploy (Platform Setup, the optional live-drift row), not on the first pass through this guide. **Managed as code** for the providers, the accounts and their bindings (1 and 2).

The weekly `drift.yml` compares each environment with the render of its latest deployment. It acts per environment as a drift reader that can connect and read, and nothing else: no write verb, so no `kubectl diff` (its server-side dry run needs `patch`), and no right on Secrets at all, since a `list` returns their data and Helm's release Secrets hold the rendered values. Each environment gets a provider, a service account, a Kubernetes Role from the bootstrap and a GitHub environment `drift-<env>` of its own.

#### 1. The providers

One provider per environment, in the same pool, with the issuer and attribute mapping of 12.2. Each admits only `drift.yml` on `main`, in that environment's `drift-<env>` GitHub environment:

```bash
for env in dev qa production; do
  condition="assertion.repository_owner_id=='$GH_OWNER_ID' && assertion.repository_id=='$GH_REPO_ID' && assertion.environment=='drift-$env' && assertion.job_workflow_ref.matches('^$GH_REPO/\\\\.github/workflows/drift\\\\.yml@refs/heads/main\$')"
  gcloud iam workload-identity-pools providers create-oidc "github-oidc-drift-$env" \
    --location=global --workload-identity-pool=github \
    --display-name="GitHub OIDC (drift $env)" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_owner_id=assertion.repository_owner_id,attribute.repository_id=assertion.repository_id,attribute.job_workflow_ref=assertion.job_workflow_ref,attribute.ref=assertion.ref" \
    --attribute-condition="$condition"
done
```

The condition, as Google stores it for `dev`:

```text
assertion.repository_owner_id=='<OWNER_ID>' && assertion.repository_id=='<REPO_ID>' && assertion.environment=='drift-dev' && assertion.job_workflow_ref.matches('^<owner>/<repo>/\\.github/workflows/drift\\.yml@refs/heads/main$')
```

**Check:** each stored condition names its own environment and `drift.yml` only:

```bash
for env in dev qa production; do
  actual="$(gcloud iam workload-identity-pools providers describe "github-oidc-drift-$env" \
    --location=global --workload-identity-pool=github --format='value(state,attributeCondition)')"
  [[ "$actual" == ACTIVE*"environment=='drift-$env'"*"drift\\\\.yml@refs/heads/main"* ]] \
    && echo "$env: condition matches" || echo "$env: DIFFERENT: compare by eye with the block above"
done
# → dev: condition matches   (and qa, production)
```

#### 2. The drift readers

A service account per environment, which only that environment's `drift-<env>` job may act as, with only the role that connects to the cluster (`$DEPLOYER_CLUSTER_ROLE`, 12.3), never `roles/container.viewer` or `roles/container.developer`:

```bash
for env in dev qa production; do
  sa="gh-drift-$env"
  gcloud iam service-accounts create "$sa" --display-name="GitHub Actions drift reader ($env)"
  gcloud iam service-accounts add-iam-policy-binding "$sa@$PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/iam.workloadIdentityUser" \
    --member="principal://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/subject/repo:$GH_REPO:environment:drift-$env"
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --condition=None \
    --member="serviceAccount:$sa@$PROJECT_ID.iam.gserviceaccount.com" --role="$DEPLOYER_CLUSTER_ROLE"
done
```

**Check:** each account holds one project role, the connect role:

```bash
for env in dev qa production; do
  echo "$env: $(gcloud projects get-iam-policy "$PROJECT_ID" --flatten=bindings \
    --filter="bindings.members:serviceAccount:gh-drift-$env@$PROJECT_ID.iam.gserviceaccount.com" \
    --format='value(bindings.role)' | tr '\n' ' ')"
done
# → dev: <the DEPLOYER_CLUSTER_ROLE value>   (and qa, production; nothing else)
```

#### 3. The drift Role comes from the bootstrap

Set each environment's drift reader in `infra/k8s/bootstrap/values.<env>.yaml` (`drift.name: gh-drift-<env>@<PROJECT_ID>.iam.gserviceaccount.com`; the bootstrap binds it as a Kubernetes `User`), then re-run the bootstrap of [13.2](#132-the-namespaces-and-the-deployer-rbac-come-from-the-bootstrap). It adds, in each of the environment's namespaces, the `Role` and `RoleBinding` `bootstrap-drift`: `get` and `list` on exactly the kinds the chart renders there, and nothing on Secrets ([`drift-rbac.yaml`](../infra/k8s/bootstrap/templates/drift-rbac.yaml); a test keeps the list equal to the render). Unset, the bootstrap binds no drift reader.

**Check:** the drift identity can read the app's objects, cannot write, and cannot read a Secret. As an admin:

```bash
SERVICE_NAME="your-service-name"   # the SERVICE_NAME repository variable (13.1)
for env in dev qa production; do
  who="gh-drift-$env@$PROJECT_ID.iam.gserviceaccount.com" ns="$SERVICE_NAME-$env"
  echo "$env:" \
    "$(kubectl auth can-i list deployments.apps -n "$ns" --as="$who")" \
    "$(kubectl auth can-i patch deployments.apps -n "$ns" --as="$who")" \
    "$(kubectl auth can-i delete configmaps -n "$ns" --as="$who")" \
    "$(kubectl auth can-i get secrets -n "$ns" --as="$who")" \
    "$(kubectl auth can-i list secrets -n "$ns" --as="$who")"
done
# → yes no no no no   (three times)
```

#### 4. The GitHub environments and their variables

Create the environments `drift-dev`, `drift-qa` and `drift-production` (**Settings → Environments → New environment**; the settings script doesn't, as with `preview`): **no required reviewers**, so the weekly run never waits, and deployment branches limited to `main`, the one ref the providers admit. Give each its own two variables; the cluster variables (`GCP_PROJECT_ID`, `GKE_CLUSTER`, `GKE_LOCATION`) come from the repository. With the GitHub CLI:

```bash
for env in dev qa production; do
  gh api -X PUT "repos/$GH_REPO/environments/drift-$env" \
    -F 'deployment_branch_policy[protected_branches]=false' -F 'deployment_branch_policy[custom_branch_policies]=true' >/dev/null
  gh api -X POST "repos/$GH_REPO/environments/drift-$env/deployment-branch-policies" -f name=main -f type=branch >/dev/null
  gh variable set DRIFT_WIF_PROVIDER --env "drift-$env" --repo "$GH_REPO" \
    --body "projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/providers/github-oidc-drift-$env"
  gh variable set DRIFT_SA --env "drift-$env" --repo "$GH_REPO" --body "gh-drift-$env@$PROJECT_ID.iam.gserviceaccount.com"
done
```

**Check:** run **Actions → Live drift → Run workflow** from `main`. The run is green; each `Drift (<env>)` job logs in, and its summary lists the findings or says `no drift`, with what it didn't compare and why (Jobs, Helm hooks, Secrets). An environment you skipped says `skipped` and which variable is missing. Findings open or update one issue per environment, labelled `drift`.

## 13. The end-to-end check from GitHub

> **After the first push:** `scripts/github-settings.sh --vars-file <file>` can set the variables of 13.1 (it never sets a secret), or set them by hand; then the `check` run of 13.3. **Do by hand:** the pull secret of 13.4.

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

Also set `SERVICE_NAME` (repository variable) to your service's name. It decides the Helm release name, and the namespaces are `<SERVICE_NAME>-<env>`: `<SERVICE_NAME>-dev`, `<SERVICE_NAME>-qa` and `<SERVICE_NAME>-production`. A database namespace `<SERVICE_NAME>-<env>-db` exists only where the chart's in-cluster PostgreSQL is used (dev and qa by default); the bootstrap of 13.2 creates it. The deploy and ops workflows compute the namespace the same way, so the value must be the same everywhere.

### 13.2 The namespaces and the deployer RBAC come from the bootstrap

**Don't create the namespaces by hand.** The chart creates none (`namespaces.create: false`) and the deploy doesn't pass `--create-namespace`. The bootstrap creates them with the restricted Pod Security labels, a quota and a LimitRange, plus the deployer RBAC of 12.3. Set each environment's deployer in `infra/k8s/bootstrap/values.<env>.yaml` (`deployer.name: gh-deployer-<env>@<PROJECT_ID>.iam.gserviceaccount.com`; the bootstrap binds that name as a Kubernetes `User`, the default `deployer.kind`), then, as a cluster admin:

```bash
SERVICE_NAME="your-service-name"   # the SERVICE_NAME repository variable (13.1)
for env in dev qa production; do
  scripts/k8s-bootstrap.sh "$env" --release "$SERVICE_NAME" > "bootstrap-$env.yaml"   # renders only
  kubectl apply -f "bootstrap-$env.yaml"
done
```

Each deployer can then deploy only in its own environment's namespaces:

```bash
for env in dev qa production; do
  echo "$env: $(kubectl auth can-i create deployments -n "$SERVICE_NAME-$env" --as="gh-deployer-$env@$PROJECT_ID.iam.gserviceaccount.com")" \
    "$(kubectl auth can-i create resourcequotas -n "$SERVICE_NAME-$env" --as="gh-deployer-$env@$PROJECT_ID.iam.gserviceaccount.com")"
done
# → yes no   (three times)
```

What it creates and why: [Infrastructure: namespace bootstrap](./infrastructure.md#namespace-bootstrap-one-time-admin-step). Deploys stay off (`DEPLOY_ENABLED` unset) until the platform rollout starts.

### 13.3 The first check: `deploy-ops` `check`

> **After the first push.** A manually started workflow appears only once it is on the default branch, and the code isn't pushed yet. Until then, the laptop-side proof of 12.2 is the strongest check you can run.

There is no separate check workflow. The `check` operation of `deploy-ops.yml` does it: it checks the environment name, logs in through Workload Identity Federation, gets the cluster credentials through the DNS-based endpoint, then runs `kubectl version` and `kubectl auth can-i --list -n <SERVICE_NAME>-<env>`. It's read-only, needs no confirmation, and holds the same lock as the deploys.

Run it from **Actions → Deploy operations → Run workflow**, with `operation: check` and the environment. For `production`, run it from a `v*` tag ref (the environment's branch policy refuses `main`); its first `check` therefore waits for the first `v*` tag that contains the operation, with `DEPLOY_ENABLED` still unset.

**Check:** the run is green, and its log shows:
- a server version from `kubectl version`;
- a short `can-i --list` for the namespace: the default rights every authenticated user has, possibly with a "may be incomplete" warning. Before the namespace and RBAC bootstrap the deployer has no rights of its own in that namespace, so a short list is expected, and the run is still green. A server version plus that list prove the identity chain.

If a step fails, see [Troubleshooting](#17-troubleshooting).

**Order:** set `DEPLOY_ENABLED=true` only after the first successful `check` of each environment you will deploy to, and not before the platform rollout.

### 13.4 An image pull secret for private GHCR images

> **Do by hand,** after the namespaces exist (no script creates it).

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

## 14. The app's secrets: External Secrets with Secret Manager

> **Do now** for 14.1 to 14.5, after the bootstrap of 13.2. **Managed as code** for the IAM bindings of 14.2: you can do them by hand, or leave them to your infrastructure-as-code repository. **After the first push:** the check of 14.6.

In dev, qa and production the app's Secret (`DB_USER`, `DB_PASSWORD`, `STATS_PASS`) comes from the [External Secrets Operator](https://external-secrets.io/), which reads it from Google Secret Manager and writes it into the namespace. In dev and qa, a second `ExternalSecret` writes the same `DB_PASSWORD` into the database namespace for the in-cluster PostgreSQL, so the app and the database share one value. The chart renders the `ExternalSecret`s; this section sets up what they read from. The chart's values are described in [Security, External Secrets Operator](./security.md#external-secrets-operator).

```text
 namespace your-service-name-dev                         Google Secret Manager
   ExternalSecret ──► SecretStore "secret-manager"           your-service-name-dev-db-user       ✓
                        │ uses the namespace's own            your-service-name-dev-db-password   ✓
                        │ ServiceAccount "external-secrets"   your-service-name-dev-stats-pass    ✓
                        ▼                                     your-service-name-production-…      ✗
   Workload Identity principal ns/your-service-name-dev/sa/external-secrets:
   Secret Accessor only on names that start with "your-service-name-dev-" (an IAM condition)
```

**Why this design:**
- **An environment reads only its own secrets.** Each namespace has its own store and its own identity, and IAM grants that identity Secret Accessor only on secret names that start with `<service>-<env>-`. A dev `ExternalSecret` that names a production secret is refused by Google, whatever it asks for.
- **No Google service account and no key.** The pinned operator supports a Kubernetes service account as an IAM principal directly ([External Secrets: Google Secret Manager](https://external-secrets.io/latest/provider/google-secrets-manager/), "Using a Kubernetes service account as a GCP IAM principal"), so the namespace's own identity is the one IAM checks: no service account to impersonate, no annotation to link them, and nothing a key could be made for.
- **The deployer can't change what a store reads.** It may create and update `ExternalSecret`s in its namespaces, never a `SecretStore` or a `ClusterSecretStore` ([Infrastructure: namespace bootstrap](./infrastructure.md#namespace-bootstrap-one-time-admin-step)).
- **Switching the store later is a values change.** [OpenBao](./security.md#openbao) and [Infisical](./security.md#infisical) are documented alternatives.

### 14.1 Install the operator from its pinned chart

The chart version and its checksum are pinned in `tools/pins.tsv` (`external-secrets-chart`); `scripts/tools.sh` downloads the chart, checks its sha256 and unpacks it, and installs a Helm of its own. Run it from your clone of the repository. The values ([`deploy/external-secrets/external-secrets-values.yaml`](../deploy/external-secrets/external-secrets-values.yaml)) pin the images by digest, keep the store kinds out of every namespace's `edit` and `admin` roles, and turn the `ClusterSecretStore` controller off (`processClusterStore: false`): the operator then skips any `ExternalSecret` that names a cluster-wide store, so a deployer can reach only its namespace's own store. The chart accepts `SecretStore` only.

```bash
scripts/tools.sh install helm4 external-secrets-chart
ESO_VERSION="$(awk -F'\t' '$1 == "external-secrets-chart" {print $2}' tools/pins.tsv)"
.tools/bin/helm upgrade --install external-secrets ".tools/opt/external-secrets-chart-$ESO_VERSION/external-secrets" \
  --namespace external-secrets --create-namespace \
  -f deploy/external-secrets/external-secrets-values.yaml --wait --timeout 5m
```

To upgrade later, run the same three lines after Renovate moves the pin.

**Check:**

```bash
.tools/bin/helm -n external-secrets list --output json | jq -r '.[0].chart'
# → external-secrets-<the version in tools/pins.tsv>
kubectl -n external-secrets get deployments
# → external-secrets, external-secrets-cert-controller and external-secrets-webhook, each 1/1
kubectl get crd secretstores.external-secrets.io externalsecrets.external-secrets.io
# → both listed
```

### 14.2 One identity per namespace, limited to its environment

Turn on the Secret Manager API, then grant each namespace's `external-secrets` service account Secret Accessor under a condition on the secret names. The app namespaces read `<service>-<env>-*`; the database namespaces (dev and qa) read only `<service>-<env>-db-password`. The bindings are on the project, so each one has its own condition title.

```bash
source ~/gke-setup.env
export SERVICE_NAME="your-service-name"   # the SERVICE_NAME repository variable (13.1)
echo "export SERVICE_NAME=\"$SERVICE_NAME\"" >> ~/gke-setup.env
gcloud services enable secretmanager.googleapis.com

pool="projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$PROJECT_ID.svc.id.goog"
grant() {   # grant <namespace> <secret-name prefix>
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --role=roles/secretmanager.secretAccessor \
    --member="principal://iam.googleapis.com/$pool/subject/ns/$1/sa/external-secrets" \
    --condition="expression=resource.name.startsWith(\"projects/$PROJECT_NUMBER/secrets/$2\"),title=eso-$1" \
    --format=none
}
for env in dev qa production; do grant "$SERVICE_NAME-$env" "$SERVICE_NAME-$env-"; done
for env in dev qa; do grant "$SERVICE_NAME-$env-db" "$SERVICE_NAME-$env-db-password"; done
```

**The grant names a namespace, not a cluster.** The principal is in the project's one Workload Identity pool (`<PROJECT_ID>.svc.id.goog`), which every cluster of the project shares, so a namespace of the same name and its `external-secrets` service account in any cluster of this project get the same access ([GKE: identity sameness](https://cloud.google.com/kubernetes-engine/docs/concepts/workload-identity#identity_sameness)). With the one cluster of this guide that is exactly the five namespaces above. A second cluster in the same project (a production cluster of its own, 2.3) gets the grants for any of these names it creates: create only its own environment's namespaces there, and treat its admins as able to read those secrets; or put the second cluster in a project of its own, whose pool is separate.

**Check:** five bindings, each with its namespace's title and a condition:

```bash
gcloud projects get-iam-policy "$PROJECT_ID" --flatten=bindings \
  --filter='bindings.role=roles/secretmanager.secretAccessor' \
  --format='table(bindings.condition.title,bindings.condition.expression)'
# → eso-<service>-dev  resource.name.startsWith("projects/<number>/secrets/<service>-dev-")
#   and one row each for qa, production, dev-db and qa-db; no row without a condition
```

### 14.3 A store in each namespace

The same manifest in every namespace of every environment: a `ServiceAccount` named `external-secrets` (the identity of 14.2; no Pod runs as it) and a `SecretStore` named `secret-manager` that uses it ([`deploy/external-secrets/secretstore-secret-manager.yaml`](../deploy/external-secrets/secretstore-secret-manager.yaml); `make kubeconform` validates it against the operator's CRDs).

```bash
for ns in "$SERVICE_NAME-dev" "$SERVICE_NAME-dev-db" "$SERVICE_NAME-qa" "$SERVICE_NAME-qa-db" "$SERVICE_NAME-production"; do
  kubectl apply -n "$ns" -f deploy/external-secrets/secretstore-secret-manager.yaml
done
```

Then name the store in each environment's values: in `charts/your-service-name/values.dev.yaml`, `values.qa.yaml` and `values.production.yaml`, replace the placeholder `name: your-secret-store` under `secrets.externalSecret.secretStoreRef` with `secret-manager`. The deploy refuses the placeholder.

**Check:**

```bash
kubectl get secretstores --all-namespaces
# → secret-manager in the five namespaces, STATUS Valid, READY True
for ns in dev dev-db qa qa-db production; do
  as="gh-deployer-${ns%-db}@$PROJECT_ID.iam.gserviceaccount.com"
  echo "$ns: $(kubectl auth can-i create externalsecrets.external-secrets.io -n "$SERVICE_NAME-$ns" --as="$as")" \
    "$(kubectl auth can-i create secretstores.external-secrets.io -n "$SERVICE_NAME-$ns" --as="$as")"
done
# → yes no   (five times). A first "no" in dev-db or qa-db means the bootstrap of 13.2 predates the
#   database namespace's ExternalSecret right; in an app namespace, that it predates External Secrets.
#   Either way, re-run the bootstrap of 13.2.
```

### 14.4 Create the secrets

One secret per key and environment, named `<service>-<env>-<key>`: `db-user`, `db-password` and `stats-pass`. Each value is read without being shown and reaches `gcloud` on its standard input (`--data-file=-`), so it is never on a command line, in the shell history or on the screen. In production, `db-user` and `db-password` are the managed database's user and password ([Database](./database.md)). In dev and qa the in-cluster PostgreSQL is created with the user `postgresql.auth.user` of the chart's `values.yaml` (`app` unless you changed it) and the password `db-password`, so `db-user` must be that user name: the loop takes it from the chart, and asks only for the passwords (the user name is not secret).

```bash
for env in dev qa production; do
  for key in db-user db-password stats-pass; do
    name="$SERVICE_NAME-$env-$key"
    if [ "$key" = db-user ] && [ "$env" != production ]; then
      value="$(awk '/^postgresql:/ {p = 1} p && /^    user:/ {print $2; exit}' charts/your-service-name/values.yaml)"
      echo "$name: $value (postgresql.auth.user)"
    else
      printf 'Value of %s (not shown): ' "$name"
      IFS= read -rs value && echo
    fi
    printf '%s' "$value" | gcloud secrets create "$name" --replication-policy=user-managed \
      --locations="$REGION" --data-file=- --format=none
    unset value
  done
done
```

**To change `stats-pass`,** add a version the same way (`gcloud secrets versions add <name> --data-file=-`, after the same `read -rs`); the operator picks it up within the `refreshInterval` (1 hour), and the Pods read it when they next start.

**To change `db-password`, change the database first.** The in-cluster PostgreSQL takes `POSTGRES_PASSWORD` only when it creates an empty volume, and nothing in the chart runs `ALTER ROLE`; a managed database keeps its user's password until you change it. A new version alone leaves the database on the old password: the running Pods keep working until they restart, then every new Pod, the migration Job of the next deploy and (dev and qa) the backup jobs fail to log in. In this order:

```bash
env=dev   # or qa; production: see the second step
# 1. The database. dev and qa: psql prompts for the new password twice (never on a command line).
#    The Pod by the StatefulSet's labels (release SERVICE_NAME, as the deploy names it), not by a
#    guessed name: the chart's fullname is the release, or <release>-<chart> when the release
#    name does not contain the chart's.
pod="$(kubectl -n "$SERVICE_NAME-$env-db" get pods -o name \
  -l "app.kubernetes.io/instance=$SERVICE_NAME,app.kubernetes.io/component=postgresql")"
kubectl -n "$SERVICE_NAME-$env-db" exec -it "$pod" -c postgresql -- \
  psql -U app -d service_template -c '\password app'   # postgresql.auth.user and .database
#    production: the Cloud SQL user, which prompts the same way:
#    gcloud sql users set-password <user> --instance=<instance> --prompt-for-password
# 2. The secret: the same password, read without echo.
IFS= read -rs value && echo
printf '%s' "$value" | gcloud secrets versions add "$SERVICE_NAME-$env-db-password" --data-file=- --format=none
unset value
# 3. Sync now instead of within the hour, then restart the backend.
for ns in "$SERVICE_NAME-$env" "$SERVICE_NAME-$env-db"; do   # production: the app namespace only
  kubectl -n "$ns" annotate externalsecrets --all force-sync="$(date +%s)" --overwrite
done
kubectl -n "$SERVICE_NAME-$env" rollout restart deployment -l app.kubernetes.io/component=backend
```

**Check:** `kubectl -n "$SERVICE_NAME-$env" rollout status deployment -l app.kubernetes.io/component=backend` completes (the readiness probe queries the database with the new password).

**Check:** nine names, and no value printed:

```bash
gcloud secrets list --filter="name~/secrets/$SERVICE_NAME-" --format='value(name.basename())' | sort
# → <service>-dev-db-password … <service>-qa-stats-pass   (9 lines)
```

### 14.5 Dev cannot read a production secret

Prove the conditions before a deploy relies on them: one `ExternalSecret` in the dev namespace reads a dev secret, and one asks for a production secret.

```bash
probe() {   # probe <name> <remote key>
kubectl -n "$SERVICE_NAME-dev" apply -f - <<EOF
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: $1
spec:
  secretStoreRef: {name: secret-manager, kind: SecretStore}
  refreshInterval: 1h
  target: {name: $1}
  data:
    - secretKey: value
      remoteRef: {key: $2}
EOF
}
probe probe-own "$SERVICE_NAME-dev-db-user"
probe probe-production "$SERVICE_NAME-production-db-user"
kubectl -n "$SERVICE_NAME-dev" wait externalsecret/probe-own --for=condition=Ready --timeout=60s
kubectl -n "$SERVICE_NAME-dev" wait externalsecret/probe-production --for=condition=Ready=false --timeout=60s
kubectl -n "$SERVICE_NAME-dev" get externalsecrets probe-own probe-production
kubectl -n external-secrets logs deployment/external-secrets --tail=500 \
  | grep '"name":"probe-production"' | grep -m1 -o 'PermissionDenied'
kubectl -n "$SERVICE_NAME-dev" delete externalsecret probe-own probe-production
```

**Check:** `probe-own` shows `SecretSynced`, READY `True`; `probe-production` shows `SecretSyncedError`, READY `False`, and the operator's log line prints `PermissionDenied` (its event only says it could not get the data). The `delete` removes both, and the Secret `probe-own` made with them (it held only the dev database user name). If `probe-production` synced, a binding of 14.2 has no condition or a wrong one: fix it before any deploy.

### 14.6 After the first deploy: every ExternalSecret is ready

> **After the first push.** The deploys render the `ExternalSecret`s once `DEPLOY_ENABLED` is set (13.3).

**Check:**

```bash
kubectl get externalsecrets --all-namespaces
# → <service>-secret in each app namespace and <service>-db-password in the dev and qa database
#   namespaces, each STATUS SecretSynced, READY True
```

If one shows `SecretSyncedError`, `kubectl -n <namespace> describe externalsecret <name>` names what it could not read: a secret that does not exist yet (14.4), or a namespace without its binding (14.2).

## 15. Costs, pausing and teardown

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
| Internal load balancer, only for previews ([12.5](#125-optional-pull-request-previews)) | billed like the external one; see the [Cloud Load Balancing pricing](https://cloud.google.com/vpc/network-pricing) page |
| Secret Manager, for the app's secrets ([14](#14-the-apps-secrets-external-secrets-with-secret-manager)) | cents: nine secrets, each with one active version in one region, and the operator's reads once an hour; see the [Secret Manager pricing](https://cloud.google.com/secret-manager/pricing) page |

### Pause and resume

```bash
# Pause: turn the autoscaler off first, or it adds a node back for the system Pods.
gcloud container node-pools update default-pool --cluster="$CLUSTER" --location="$ZONE" --no-enable-autoscaling
gcloud container clusters resize "$CLUSTER" --location="$ZONE" --node-pool=default-pool --num-nodes=0 --quiet

# Resume: two nodes, then the autoscaler back on with the limits of 8.2.
gcloud container clusters resize "$CLUSTER" --location="$ZONE" --node-pool=default-pool --num-nodes=2 --quiet
gcloud container node-pools update default-pool --cluster="$CLUSTER" --location="$ZONE" \
  --enable-autoscaling --min-nodes=1 --max-nodes=3
```

**Check:** a few minutes after the pause, `kubectl get nodes` prints `No resources found`; after the resume, 2 nodes are `Ready`.

While paused, the Tunnel's connector is down too, so visitors get Cloudflare error 1033.

While paused you pay only for disks that still exist, and for the load balancer if one is in use.

### Teardown

Teardown deletes everything this guide created. Run it in this order:

If you made a Gateway (9.4 or 12.5), delete it first, so GKE removes its load balancer: `kubectl delete gateway --all -n gateway-system`. Then wait until the Gateway's forwarding rules are gone (other rules in the project are not yours to wait for):

```bash
gcloud compute forwarding-rules list --filter="name~^gkegw" --format='value(name)'
# → nothing (rerun after a minute if a name is still listed)
```

```bash
gcloud container clusters delete "$CLUSTER" --location="$ZONE" --quiet
gcloud artifacts repositories delete "$AR_REPO" --location="$REGION" --quiet
gcloud compute routers nats delete apps-nat --router=apps-router --region="$REGION" --quiet
gcloud compute routers delete apps-router --region="$REGION" --quiet
gcloud compute networks subnets delete "$SUBNET" --region="$REGION" --quiet
gcloud compute networks subnets delete apps-proxy-only --region="$REGION" --quiet   # if 12.5 made it
gcloud compute networks delete "$NETWORK" --quiet
gcloud iam workload-identity-pools delete github --location=global --quiet   # kept for 30 days: the name stays reserved until it is purged
for env in dev qa production; do gcloud iam service-accounts delete "gh-deployer-$env@$PROJECT_ID.iam.gserviceaccount.com" --quiet; done
gcloud secrets list --filter="name~/secrets/$SERVICE_NAME-" --format='value(name.basename())' \
  | while read -r name; do gcloud secrets delete "$name" --quiet; done   # the secrets of 14.4
gcloud iam service-accounts delete "$NODE_SA" --quiet
```

In the service-account loop, add `preview` to the list if 12.5 made it. If 12.3 created the custom role: `gcloud iam roles delete gkeDnsConnect --project="$PROJECT_ID" --quiet`.

Remove the project-level role bindings too (the Secret Accessor bindings of 14.2 name namespaces of the deleted cluster), or simply delete the whole project.

## 16. Final checklist and the values to hand over

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
| 13 | Dev can't read a production secret | step 14.5 check | `probe-production` `SecretSyncedError` |
| 14 | End-to-end from GitHub, **after the first push** | step 13.3 | green `deploy-ops` `check` |
| 15 | Every `ExternalSecret` synced, **after the first deploy** | step 14.6 check | `SecretSynced`, READY `True` |

When rows 1 to 13 pass (leaving out optional row 9 if you skipped section 10), you're ready for the first push: set the values of [13.1](#131-variables-and-secrets) in GitHub (none is secret), push, then run row 14, and row 15 after the first deploy.

## 17. Troubleshooting

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
| `Maintenance policy would go longer than 32d without 48h maintenance availability` | the maintenance window is too short | widen it, as in [8.2](#82-create-it) |
| `kubectl` hangs or times out | wrong kubeconfig, or the cluster is resized to 0 | `source ~/gke-setup.env`, then check `kubectl config current-context` and the node count |

## 18. What comes next

> **Planned changes.** 18.1 is a status table and 18.2 is a step of the platform rollout. The rest of 18.3 is not implemented yet, except the settings script, which is in the repository.

### 18.1 Before production: what is done and what is still open

| Area | Status | Where |
|---|---|---|
| private nodes, Dataplane V2, Workload Identity, Shielded nodes, the release channel, keyless CI, per-namespace deploy rights | done in this guide | 6 to 13 |
| the app's secrets in Secret Manager, each environment reading only its own | done in this guide | 14 |
| closing the public IP endpoint | a step of the platform rollout | [18.2](#182-close-the-public-ip-endpoint-at-the-platform-rollout) |
| signed images only, on this cluster | **open**: the example policies are tested on kind ([Admission control](./admission-control.md)); installing Kyverno here (its chart version is pinned in `tools/pins.tsv`), first in Audit and then Enforce, or Binary Authorization, is not written up yet | — |
| backups | **open**: the chart's in-cluster PostgreSQL backup runs ([Disaster Recovery](./disaster-recovery.md)); Backup for GKE and an off-site copy ([Cloudflare §12](./cloudflare-setup.md#12-optional-a-private-r2-bucket-for-off-site-backups)) aren't set up by this guide | — |
| the production database | **open**: Cloud SQL for PostgreSQL, private, with IAM authentication | [Database](./database.md) |
| alerts and uptime checks | **partly**: the chart's alert rules with `monitoring.flavour: gmp` ([Alerts](./operations-runbook.md#alerts)); where they notify you, and uptime checks, aren't set up by this guide | — |
| audit and flow logs | **open**: VPC flow logs, and Data Access audit logs for the GKE API | the infrastructure-as-code repository |
| one shared zonal cluster | a choice | [2.3](#23-decisions-to-make-now) |

### 18.2 Close the public IP endpoint (at the platform rollout)

```bash
gcloud container clusters update "$CLUSTER" --location="$ZONE" --enable-private-endpoint
```

This turns off the external IP endpoint and keeps the internal one, which the nodes use. CI and your laptop (9) already use the DNS-based endpoint, so nothing else changes. `--no-enable-ip-access` is stricter: it also turns off the internal IP endpoint; read Google's [network isolation how-to](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/latest/network-isolation) before you use it. Never allowlist CI addresses instead.

**Check:** `gcloud container clusters describe "$CLUSTER" --location="$ZONE" --format='yaml(controlPlaneEndpointsConfig.ipEndpointsConfig)'` shows `enablePublicEndpoint: false`; `kubectl get nodes` still works; a `deploy-ops` `check` ([13.3](#133-the-first-check-deploy-ops-check)) is still green.

### 18.3 The rest

- **Settings script and release App (in the repository):** [`scripts/github-settings.sh`](../scripts/github-settings.sh) replaces the hand steps of 11.2 and adds the `release` environment and the release GitHub App's tag ruleset ([GitHub repository settings](./github-repository-settings.md)).
- **Domain, TLS and public traffic:** through Cloudflare. See [Cloudflare Setup](./cloudflare-setup.md): a Tunnel by default, so the cluster has no public load balancer. The alternative is a `Gateway` with a Google-managed certificate.
- **The edge mode:** the template's default `edge.mode` is `gateway`. A platform that uses Cloudflare Tunnel sets `edge.mode: tunnel` in its own environment values (9.4; [Infrastructure, Edge](./infrastructure.md#edge)).
- **Other services:** each packaged as a Helm chart and deployed to this same cluster.
- **Infrastructure as code:** everything in this guide expressed in OpenTofu or Terraform, with state in a locked GCS bucket, so the cluster can be rebuilt from Git.
