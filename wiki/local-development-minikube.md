# Local Deployment (Minikube / kind)

Complete guide to running the three-image stack (**backend** / **frontend** /
**nginx** + PostgreSQL) on a local cluster. **Minikube and kind are the two
first-class local clusters** here (any Kubernetes works too) — and **every step
has three interchangeable ways to do it**:

| Option | What it is | When to use it |
| --- | --- | --- |
| **① Manual** | The raw `docker` / `kubectl` / `helm` commands, run by hand. | You want to see and control every command (learning, debugging, no tooling). |
| **② `make`** | A one-line Makefile target that wraps the script. | Daily driver — fastest, least to type. |
| **③ Bash script** | The config-driven operational tool in `scripts/`. | You want dry-runs, secret-safety, `--only` scoping, and clear framed output. |

**Every `make` target and every script resolves the namespace, image tag and
secrets from the chart's `values.<env>.yaml`** — so the three paths can never
drift from the manifests. `--env local` (laptop cluster, bare `:local` image
tags read from the node daemon) is used throughout.

> **Steps 1–3 are one-time environment setup** — installing the tools, starting
> the cluster, and providing the DB password. These are **manual only**: there is
> no `make` target or script for installing the cluster tooling itself. From
> **step 4 on, all three options are available for every step.**
>
> **Minikube vs kind** — both run the whole stack; pick one and stick with it:
> - **Minikube** is turnkey: `minikube docker-env` builds images straight into the
>   node, `minikube service` opens a URL, and `minikube addons` installs Ingress.
> - **kind** is fast and spec-driven, but has **no `docker-env`, `service`, or
>   addons** — you `kind load docker-image` to get images onto the node and
>   `kubectl port-forward` (step 7) to reach the services. kind draws CPU/RAM from
>   the host Docker daemon, so raise that limit for the full stack.
>
> The `make` targets and `scripts/` are cluster-agnostic (`kubectl` / `helm`), so
> options **② and ③ are identical on both** — only the **① Manual** commands
> differ, and each difference is flagged below.
>
> These are **local developer tools**, not production deployment automation. They
> favour clear output and safety (dry-runs, confirmations, no committed secrets)
> over raw speed.

---

## Workflow

1. [Prerequisites](#step-1-prerequisites) *(manual)*
2. [Start the cluster](#step-2-start-the-cluster) *(manual)*
3. [Provide the DB password](#step-3-provide-the-db-password-one-time) *(manual)*
4. [Build the images into the node daemon](#step-4-build-the-images-into-the-node-daemon)
5. [Deploy the stack](#step-5-deploy-the-stack)
6. [Verify the deployment](#step-6-verify-the-deployment)
7. [Access the services](#step-7-access-the-services)
8. [Watch the logs](#step-8-watch-the-logs)
9. [Iterate after a code change — rebuild + roll](#step-9-iterate-after-a-code-change--rebuild--roll)
10. [Smoke-test the HTTP surface](#step-10-smoke-test-the-http-surface)
11. [Manage secrets](#step-11-manage-secrets)
12. [Database backup](#step-12-database-backup)
13. [Clean up](#step-13-clean-up)
- [Optional: Monitoring](#optional-enable-monitoring)
- [Optional: Ingress / HTTPS](#optional-ingress--https)
- [Optional: Runtime security (DAST + WAF)](#optional-runtime-security-dast--waf)
- [Troubleshooting](#troubleshooting)

---

## Step 1: Prerequisites

Install the tooling once. **Manual only** (no `make`/script installs the cluster).

```bash
# Required: Docker (or Podman), a local cluster (Minikube *or* kind), kubectl, Helm
# Recommended: stern (multi-pod log tailing), yq (reads values.<env>.yaml)
```

### Linux (Ubuntu/Debian)

```bash
# Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"; newgrp docker

# Local cluster — pick one:
# Minikube
curl -LO https://storage.googleapis.com/minikube/releases/latest/minikube-linux-amd64
sudo install minikube-linux-amd64 /usr/local/bin/minikube
# kind
curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.24.0/kind-linux-amd64
chmod +x ./kind && sudo mv ./kind /usr/local/bin/kind

# kubectl
curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl

# Helm
curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
```

### macOS

```bash
brew install minikube kind kubectl helm docker
```

Verify:

```bash
kubectl version --client && helm version && docker version
minikube version    # if you installed Minikube
kind version        # if you installed kind
```

---

## Step 2: Start the cluster

**Manual only.** Create a local cluster with enough resources for the full
stack, then verify it is reachable.

**Minikube** — give it generous resources (the stack is backend + frontend +
nginx + PostgreSQL):
```bash
minikube start --cpus=4 --memory=4096 --disk-size=20g --driver=docker
```

**kind** — a single node draws CPU/RAM from the host Docker daemon, so raise that
limit first (Docker Desktop → Settings → Resources, or the daemon defaults); then:
```bash
kind create cluster --name your-service-name
# Multi-node / custom resources: kind create cluster --config kind.yaml
```

**Verify** (identical once `kubectl` points at the cluster):
```bash
kubectl get nodes
kubectl cluster-info
minikube status    # Minikube only
kind get clusters  # kind only — lists created clusters
```

> Any cluster works — the steps below were written `minikube`-first but the
> `kubectl`/`helm` commands are identical on kind, k3d, or a remote cluster with
> a configured kubeconfig. The only per-cluster differences are called out
> explicitly (image loading in steps 4 & 9, service URLs in step 7, teardown in
> step 13).

---

## Step 3: Provide the DB password (one-time)

The chart's `secrets.dbPassword` defaults to empty, and it is **also** the
password the internal PostgreSQL initializes with (single source — the app and
the database must match). So copy the gitignored example, fill it in, and layer
it on every deploy with an extra `-f`. **This file is gitignored — never commit
a real password.**

```bash
cp charts/your-service-name/values.secret.yaml.example \
   charts/your-service-name/values.secret.yaml
# Edit values.secret.yaml → set secrets.dbPassword (and STATS_PASS if present).
```

This one file is read by **all three** deploy paths below, so set it once.

---

## Step 4: Build the images into the node daemon

The `local` values use **bare `:local` tags** (`imagePullPolicy: IfNotPresent`)
so the kubelet resolves the images built straight into the node daemon — no
registry. **Building images is a Docker concern, so the fresh-build path is
manual**; `rebuild-and-roll` (step 9) re-runs this build as part of its
build→verify→roll→health loop after the first deploy.

### ① Manual

Build the three images, then get them onto the cluster node — **this is the one
step that differs by cluster** (the `:local` tag + `IfNotPresent` mean the
kubelet reads them straight off the node daemon, no registry):

```bash
# Build on the host daemon (both clusters):
docker build -t your-service-name-backend:local  -f Dockerfile.backend  .
docker build -t your-service-name-frontend:local -f Dockerfile.frontend .
docker build -t your-service-name-nginx:local    -f Dockerfile.nginx    .

# Minikube: point the shell at the node daemon so builds land there directly
# (if you already built on the host daemon, `minikube image load <name>:local`):
eval "$(minikube docker-env)"

# kind: the node is its own container — load the images you just built into it
kind load docker-image your-service-name-backend:local
kind load docker-image your-service-name-frontend:local
kind load docker-image your-service-name-nginx:local

docker images | grep your-service-name     # confirm the three :local images
```

### ② make

No build-only target (image build is bundled into `rebuild-and-roll`, which is
the *iterate* step — see step 9). Use **① Manual** for the first build, or:

```bash
make rebuild-and-roll HELM_ENV=local   # build + verify + roll + health (needs an existing release)
```

### ③ Bash script

```bash
./scripts/rebuild-and-roll.sh --env local   # build + verify + roll + health-check each service
```

---

## Step 5: Deploy the stack

The stack is deployed with the **Helm chart** (`charts/your-service-name`), not
`kubectl apply`. The chart creates **both** namespaces (app + `…-db`), renders
the app Secret and the `…-db-credentials` Secret from `values.local.yaml` + your
`values.secret.yaml`, and runs the DB migration as a **`post-install` hook** that
auto-deletes on success — so you create nothing by hand.

- **`make deploy` / `e2e.sh deploy`** = the full gate: apply **and** in-cluster
  verify (Postgres Running, migration completed, backend on real Postgres).
- **`make k8s-apply` / `k8s-env.sh apply`** = apply only (no verify).

### ① Manual

```bash
helm upgrade --install your-service-name charts/your-service-name \
  -f charts/your-service-name/values.local.yaml \
  -f charts/your-service-name/values.secret.yaml \
  -n your-service-name-local --create-namespace
```

### ② make

```bash
# Full deploy + verify (recommended):
make deploy HELM_ENV=local \
  HELM_ARGS="-f charts/your-service-name/values.secret.yaml"

# Apply only (no verify):
make k8s-apply HELM_ENV=local \
  HELM_VALUES="-f charts/your-service-name/values.secret.yaml"
```

### ③ Bash script

```bash
# Full deploy + verify:
./scripts/e2e.sh deploy --env local \
  --values charts/your-service-name/values.secret.yaml

# Apply only:
./scripts/k8s-env.sh apply --env local \
  --values charts/your-service-name/values.secret.yaml
```

---

## Step 6: Verify the deployment

### ① Manual

```bash
# Pods, services, and the one-shot migration job
kubectl get pods        -n your-service-name-local
kubectl get svc         -n your-service-name-local
kubectl get jobs        -n your-service-name-local      # migration auto-deletes on success
kubectl get pods        -n your-service-name-local-db

# Wait for Ready (blocks until the rollout is complete)
kubectl wait --for=condition=ready pod \
  -l app.kubernetes.io/name=your-service-name \
  -n your-service-name-local --timeout=120s

# Health checks
curl http://localhost:8000/api/v1/health
kubectl exec -n your-service-name-local-db your-service-name-postgresql-0 -- \
  pg_isready -U app -d service_template
```

### ② make

```bash
make k8s-status HELM_ENV=local    # deployments + pods + services + per-service rollout status
```

### ③ Bash script

```bash
./scripts/k8s-env.sh status --env local   # one-glance rollup + rollout status per service
# or the strict in-cluster gate (Postgres/migration/backend checks, exits non-zero on FAIL):
./scripts/e2e.sh verify --env local
```

---

## Step 7: Access the services

### ① Manual

```bash
# One tunnel per service, in separate terminals (or background with &)
kubectl port-forward -n your-service-name-local svc/your-service-name-backend  8000:8000
kubectl port-forward -n your-service-name-local svc/your-service-name-frontend 3000:3000
kubectl port-forward -n your-service-name-local svc/your-service-name-nginx    8081:8081
```

Then:

| Surface | URL |
| --- | --- |
| Backend API | http://localhost:8000/api/v1/ |
| Backend docs | http://localhost:8000/docs |
| Frontend | http://localhost:3000/ |
| Nginx static | http://localhost:8081/ |

### ② make

```bash
make k8s-port-forward HELM_ENV=local K8S_PORT=8000   # backend only
# All services (self-cleaning — kills every tunnel on Ctrl-C / exit):
make k8s-port-forward-all HELM_ENV=local
```

### ③ Bash script

The wrapper (`scripts/port-forward.sh`) forwards every service in
`scripts/services.yaml` and kills all tunnels on Ctrl-C / exit. `--port key=lp`
overrides the *local* port for one service — repeat it, one `key=lp` per service:

```bash
# All three, each on an explicit local port:
./scripts/port-forward.sh --env local \
  --port backend=8000 --port frontend=3000 --port nginx=8080
```

Omit `--port` for a service and it defaults to that service's container port, so
the bare command already lands on the standard ports below:

```bash
./scripts/port-forward.sh --env local   # backend:8000, frontend:3000, nginx:8081
```

| key | container port (in-pod) | what it is |
| --- | --- | --- |
| `backend` | 8000 | FastAPI/Uvicorn |
| `frontend` | 3000 | Next.js |
| `nginx` | 8081 | static assets |

```bash
# Backend only:
./scripts/port-forward.sh --env local --only backend --port backend=8000
# Preview the planned forwards without starting anything:
./scripts/port-forward.sh --list --env local
```

> **Cluster notes.** Minikube can open a URL directly
> (`minikube service your-service-name-backend -n your-service-name-local`) or
> expose a NodePort via `minikube ip`. **kind has no `service`** — `kubectl
> port-forward` (above) is the supported path; a real `LoadBalancer` IP needs
> metallb, which is usually more setup than local dev needs.

---

## Step 8: Watch the logs

### ① Manual

```bash
kubectl logs -n your-service-name-local -l app.kubernetes.io/component=backend  -f
kubectl logs -n your-service-name-local -l app.kubernetes.io/component=frontend -f
kubectl logs -n your-service-name-local -l app.kubernetes.io/component=nginx    -f
# Multi-pod tailing (needs `stern`):
stern -n your-service-name-local your-service-name
```

### ② make

```bash
make k8s-logs HELM_ENV=local          # last 100 lines of every service, labelled
make k8s-logs HELM_ENV=local HELM_ARGS="-f --since 5m"   # live, interleaved tail
```

### ③ Bash script

```bash
./scripts/logs.sh --env local                     # last 100 lines, all services
./scripts/logs.sh --env local --only backend --tail 300
./scripts/logs.sh -f --env local --since 5m        # live, interleaved; Ctrl-C stops all
```

---

## Step 9: Iterate after a code change — rebuild + roll

After editing code, rebuild the affected image into the node daemon, roll the
workload, and health-check a fresh pod — per service, failure on one is reported
not fatal to the others.

### ① Manual

```bash
# Minikube: rebuild straight into the node daemon
eval "$(minikube docker-env)"
# kind: build on the host, then load into the node:
#   docker build -t your-service-name-backend:local -f Dockerfile.backend .
#   kind load docker-image your-service-name-backend:local
docker build -t your-service-name-backend:local -f Dockerfile.backend .   # rebuild the changed one
kubectl set image deployment/your-service-name-backend \
  backend=your-service-name-backend:local -n your-service-name-local
kubectl rollout restart deployment/your-service-name-backend -n your-service-name-local
kubectl rollout status deployment/your-service-name-backend -n your-service-name-local --timeout=120s
curl http://localhost:8000/api/v1/health
```

### ② make

```bash
make rebuild-and-roll HELM_ENV=local               # all services
make rebuild-and-roll HELM_ENV=local HELM_ARGS="--only backend"   # one service
```

### ③ Bash script

```bash
./scripts/rebuild-and-roll.sh --env local
./scripts/rebuild-and-roll.sh --env local --only backend
./scripts/rebuild-and-roll.sh --env local --no-health --tag local   # skip health-check
```

---

## Step 10: Smoke-test the HTTP surface

The client-surface gate — exercises the live API like a real client and reports
pass/fail per check (text / JSON / JUnit for CI). Run it **after** port-forward.

### ① Manual

```bash
curl -fsS http://localhost:8000/api/v1/health
curl -fsS http://localhost:8000/api/v1/echo -d '{"message":"hi"}' -H 'Content-Type: application/json'
curl -fsS http://localhost:8000/api/v1/items
```

### ② make

```bash
make smoke-test                     # probes SMOKE_BASE_URL (default http://localhost:8000)
make smoke-test SMOKE_BASE_URL=http://localhost:8000 -- smoke-test --format junit --out smoke.xml
```

### ③ Bash script

```bash
./scripts/smoke-test.sh --base-url http://localhost:8000
./scripts/smoke-test.sh --format junit --out smoke.xml   # CI test report
```

---

## Step 11: Manage secrets

Create or update the namespace `generic` Secret from `.env` or `--key` pairs;
values are masked everywhere and `show` lists key names only.

### ① Manual

```bash
kubectl create secret generic your-service-name-local \
  --from-env-file=.env \
  -n your-service-name-local --dry-run=client -o yaml | kubectl apply -f -
```

### ② make

```bash
make secrets HELM_ENV=local                 # update from .env (keys only; values masked)
```

### ③ Bash script

```bash
./scripts/secrets.sh update --env local -f .env
./scripts/secrets.sh update --env local --key DB_PASSWORD=hunter2 --key DB_USER=app
./scripts/secrets.sh show  --env local                  # keys only
./scripts/secrets.sh delete --env local --yes
```

---

## Step 12: Database backup

On-demand PostgreSQL backup. `kubectl cp` can't reach a *completed* pod, so the
script attaches a short-lived "fetch" pod to the same backup PVC, copies the
file, and throws the pod away.

### ① Manual

```bash
kubectl exec -n your-service-name-local-db your-service-name-postgresql-0 -- \
  pg_dump -U app -d service_template > backup.sql
```

### ② make

```bash
make db-backup HELM_ENV=local
```

### ③ Bash script

```bash
./scripts/db-backup.sh --env local
./scripts/db-backup.sh --env local --no-cleanup   # keep the Job + fetch pod to debug
```

---

## Step 13: Clean up

### ① Manual

```bash
helm uninstall your-service-name -n your-service-name-local
# Helm leaves the namespaces behind on uninstall — delete them explicitly:
kubectl delete namespace your-service-name-local your-service-name-local-db
# Tear down the cluster:
minikube stop        # Minikube — preserves state
minikube delete      # Minikube — removes everything
kind delete cluster --name your-service-name   # kind — one cluster
kind delete clusters --all                     # kind — everything
```

### ② make

```bash
make k8s-delete HELM_ENV=local
```

### ③ Bash script

```bash
./scripts/k8s-env.sh delete --env local          # asks to confirm (pass --yes to skip)
./scripts/k8s-env.sh delete --env local --yes
```

---

## Optional: Enable monitoring

The chart ships a ServiceMonitor that auto-discovers backend metrics once the
Prometheus stack is present. **Manual** (no `make`/script wraps the Prometheus
install).

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
helm install monitoring prometheus-community/kube-prometheus-stack \
  --namespace monitoring --create-namespace \
  --set prometheus.service.type=NodePort --set grafana.service.type=NodePort

kubectl get servicemonitor -n your-service-name-local          # confirm discovery
kubectl port-forward -n monitoring svc/monitoring-grafana 8080:80   # kind (Minikube: `minikube service monitoring-grafana -n monitoring`)
kubectl port-forward -n monitoring svc/monitoring-prometheus 9090:9090   # then :9090/targets
```

---

## Optional: Ingress / HTTPS

The chart includes an Ingress. For local HTTPS, `certs-local.sh` wires
cert-manager's bundled `selfsigned` issuer to the chart's Ingress TLS
`secretName` + hosts. **Manual** for the addon; **script** for the cert wiring.

```bash
# ① Enable Ingress (manual) — cluster-specific:
# Minikube:
minikube addons enable ingress
kubectl get pods -n ingress-nginx
# kind: no addons — create the cluster with extraPortMappings (80/443) in
# kind.yaml, then install the controller:
#   helm install ingress-nginx ingress-nginx --repo https://kubernetes.github.io/ingress-nginx
#   (or: kubectl apply -f the ingress-nginx release-1.x deploy.yaml)

# ③ Wire self-signed TLS (local-dev only; production uses letsencrypt-prod)
./scripts/certs-local.sh --env local
# Then map the hosts (from values.local.yaml: local-api/app/root .your-service.test)
# to 127.0.0.1 and trust the CA:  mkcert -install && mkcert local-api.your-service.test ...
```

---

## Optional: Runtime security (DAST + WAF)

The running app is covered by two complementary layers — **DAST** (OWASP ZAP
*finds* web vulnerabilities by probing) and a **WAF** (an in-cluster **Coraza**
proxy that *blocks* malicious requests live). Both validate on **any** local
cluster (Minikube or kind): the WAF is a plain Deployment + Service sitting
behind the Ingress, so it behaves identically on both. Operational
steps: [`dast-waf-openshift-local-runbook.md`](./dast-waf-openshift-local-runbook.md).

### WAF — enable and prove it blocks (local)

The WAF is **off by default** (`waf.enabled: false`) — enabling it is a
`helm upgrade`, no redeploy:

```bash
helm upgrade your-service-name charts/your-service-name \
  -f charts/your-service-name/values.local.yaml \
  -f charts/your-service-name/values.secret.yaml \
  -n your-service-name-local --set waf.enabled=true
helm status your-service-name -n your-service-name-local   # wait for the WAF pod READY
```

Then prove it through your port-forward (step 7) on `localhost:8000` — the WAF
fronts the API surface (`/api`, `/docs`, `/openapi.json`):

```bash
# ① malicious → expect 403 (blocked)
curl -s -o /dev/null -w "%{http_code}\n" "http://localhost:8000/api/items?id=1' OR '1'='1"
curl -s -o /dev/null -w "%{http_code}\n" --data "q=<script>alert(1)</script>" http://localhost:8000/api/search
# ② legitimate → expect 200
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/v1/health
```

> ⚠ **Pin the WAF images first.** The Coraza Envoy base image + wasm plugin image
> in `values.yaml` are placeholders — review and pin them (digest) to a real
> Envoy+Coraza release before the WAF pod will come up `READY`, and confirm the
> wasm filter `type_url` matches the pinned plugin (runbook §4). If a malicious
> request returns `200`, traffic is bypassing the WAF — check the pod is `READY`
> and that the Ingress API paths point at `<name>-waf` (`waf.enabled` swaps the
> backend via the `chart.apiBackendService` helper).

### DAST — scan the live deploy (local)

The thin ZAP baseline runs in CI (`dast` job) against the deploy; locally you can
run the **same single-source script** against your port-forwarded API, so local ↔
CI cannot drift:

```bash
export DAST_TARGET_URL="http://localhost:8000"
export SEC_OUTPUT_DIR="$(pwd)/output/security"   # wherever your scan reports go
bash infra/security/local/scan-dast.sh "$SEC_OUTPUT_DIR" "$(pwd)"
```

Baseline (`zap-baseline.py`) is **advisory** (reports findings, exits 0). The
**full** tier — active scan, OpenAPI-driven, authenticated context — needs the
reachable deploy plus the full-DAST enhancements, and is the runbook's job.

> **Supply-chain scans locally** follow the same single-source pattern — see
> [`security.md`](./security.md) and the `devsec.sh` runner. They run against the
> built image and the committed lockfiles, not the cluster, so they are
> cluster-agnostic too.

---

## Troubleshooting

### Pod stuck in `Pending`

```bash
kubectl describe resourcequota -n your-service-name-local
kubectl describe node minikube                 # Minikube; kind: `kubectl describe node kind-control-plane`
minikube stop && minikube start --cpus=4 --memory=4096   # kind: `kind delete cluster --name your-service-name && kind create cluster --name your-service-name`
```

### `ImagePullBackOff`

The `:local` images aren't on the node daemon — rebuild them into it:

```bash
eval "$(minikube docker-env)"                                  # Minikube
docker build -t your-service-name-backend:local -f Dockerfile.backend .
minikube image load your-service-name-backend:local            # Minikube
# kind: build on the host, then `kind load docker-image your-service-name-backend:local`
```

### `CrashLoopBackOff`

```bash
kubectl logs -n your-service-name-local <pod-name> --previous
kubectl exec -n your-service-name-local <pod-name> -- env | grep -E 'DB_|BACKEND|FRONTEND'
# Migration didn't complete? Re-apply — the post-install hook re-runs automatically:
make deploy HELM_ENV=local HELM_ARGS="-f charts/your-service-name/values.secret.yaml"
```

---

## Next Steps

- [Infrastructure & Kubernetes Resources](infrastructure.md)
- [CI/CD Pipeline Documentation](ci-cd.md)
- [Operations Runbook](operations-runbook.md)
- [Containerization](containerization.md)

---

**Last Updated**: 2026-11-17
**Cluster**: Minikube / kind · **Helm**: 3.x · **Images**: bare `:local` tags off the node daemon
