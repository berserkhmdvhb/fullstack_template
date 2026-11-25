# Getting Started

Complete guide to setting up, configuring, and running the service template locally and in Kubernetes.

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Clone & Initial Setup](#clone--initial-setup)
- [Adapt the Service Name](#adapt-the-service-name)
- [Configure Environment](#configure-environment)
- [Backend Setup](#backend-setup)
- [Frontend Setup](#frontend-setup)
- [Running Locally](#running-locally)
- [Running with Docker Compose](#running-with-docker-compose)
- [Running on Kubernetes (Local)](#running-on-kubernetes-local)
- [Deploying to Kubernetes (Production)](#deploying-to-kubernetes-production)
- [Verify Everything Works](#verify-everything-works)

---

## Prerequisites

| Tool | Minimum Version | Purpose |
|------|----------------|---------|
| **Git** | 2.x | Version control |
| **Python** | 3.12 | Backend runtime |
| **Node.js** | 26 | Frontend runtime |
| **pnpm** | 12 (`make tools` installs the pinned one) | Package manager of the frontend and the docs site |
| **Docker** or **Podman** | Latest | Container runtime |
| **kubectl** | 1.34+ (within one minor of your cluster) | Kubernetes CLI |
| **Helm** | 4 (`make tools` installs the pinned one) | Deploy the chart (`helm upgrade --install`; the scripts pass Helm 4's `--force-conflicts`) |
| **make** | — | Development shortcuts (optional) |

### For Kubernetes Deployment

| Tool | Purpose |
|------|---------|
| **kind** (the default; `make tools` installs the pinned one) or **minikube** (optional) | Local Kubernetes cluster for development |
| **Helm** (optional) | Package manager for cluster-level dependencies (cert-manager and an Ingress controller) |

### Optional

- **The development container** — [Development container](#development-container) below
- **Apptainer** — For Singularity-style (rootless, single-file) container runs
- **WSL2** — Required on Windows

---

### The developer toolchain

`make tools` (or `scripts/tools.sh install [name ...]`) installs the pinned tools into `.tools/bin`, on linux x64 and
macOS arm64. It needs `bash`, `curl`, `tar`, a `python3` (3.9 is enough: macOS's own works) and a `sha256sum` or a `shasum`
(macOS has the latter), and nothing else: no mise, no uv, no Python of 3.12 or later. mise's own download is a bare
executable, so nothing is extracted until uv has installed the pinned Python. It writes only inside the repository, never in your home directory.

- **Where the versions are.** [`mise.toml`](../mise.toml) lists the tools and their versions.
  [`mise.lock`](../mise.lock) holds, for each, the exact download url and its sha256 for both platforms, and a signature or attestation where the vendor publishes one. Installs run `mise install --locked`, so a
  changed byte in a download or in the lock refuses the install and links nothing; every installed tree is then
  checked for links out of it and setuid files. [`tools/pins.tsv`](../tools/pins.tsv) keeps what mise does not
  install: mise itself (the bootstrap, checksum-verified), Python and the four uv tools (hash-pinned requirements), and
  two data rows. [ADR-0043](./adr/0043-mise-for-the-developer-toolchain.md) says why.
- **Run mise through `scripts/tools.sh mise ...`**, for example `scripts/tools.sh mise ls` or
  `scripts/tools.sh mise doctor`. It runs the pinned mise with this repository's directories under `.tools/`, ignores
  your own mise configuration and any `MISE_*` or GitHub token in your environment. The bare `.tools/bin/mise` would
  write `~/.cache/mise` and `~/.local/state/mise`.
- **Your own mise** (`mise activate`) is not needed. If you have it, `mise.toml`'s `[env]` puts `.tools/bin` on `PATH`
  inside the repository; `.env` is read by the app itself and is not loaded by mise.
- **Updating.** Renovate bumps a version in `mise.toml` and runs `mise lock`, so `mise.toml` and `mise.lock` change in
  the same pull request. Except for four lines, `helm3`, `helm4`, `kubectl` and `kubectl-floor`: Renovate's mise manager
  cannot read them, so regex managers move them and no `mise lock` runs. Those pull requests change `mise.toml` only,
  say so in their description, and the `pins` check is red until a maintainer runs `make pins-refresh` and pushes
  `mise.lock` (or the weekly lock maintenance does). Nothing writes to a pull request branch: the jobs stay
  `contents: read`. By hand: change the version (a release at least 7 days old), run `make pins-refresh`, commit
  both files. `mise lock` makes about 50 of the 60 GitHub API calls an unauthenticated address gets per hour: wait for
  the limit to reset if it stops, and do not use a personal token. Renovate runs it with its own token, and needs mise
  2026.7.12 or newer ([`renovate.json5`](../renovate.json5) says what else an adopter turns on).
- **The check.** The `pins` job (and `make pins-refresh` without `--check`) fails when `mise.toml` names a version
  `mise.lock` does not hold, or an entry lacks a sha256 for either platform; that part is offline. It also compares
  each `github:` tool's locked sha256 with the vendor's own checksum file where one exists. After a pull request that
  changes a tool's lock entry, the first `make tools` reinstalls that tool.

### Development container

`.devcontainer/devcontainer.json` is an optional container with everything above: its base image (the OS of the kind
job's runner, [`reusable-kind.yml`](../.github/workflows/reusable-kind.yml)) and its one feature are pinned by digest,
and on creation it runs `make tools`, so Python, Node, Helm, kind and the security tools come from `mise.toml`, `mise.lock` and `tools/pins.tsv`
([how](#the-developer-toolchain)), checksum-verified, and `.tools/bin` is on `PATH`. Nothing in it restates a version, and Renovate updates its digests.
`.tools` and `.venv` are volumes of the container, not the workspace's: the installer links absolute paths, so a host
toolchain and the container's would break each other. The container is linux/amd64 only: the toolchain is locked for
linux x64 and macOS arm64 already, so on Apple Silicon run `make tools` natively on macOS.

Two pieces are not pinned: the Docker-in-Docker feature installs the current Docker engine and CLI when the container
is built, and apt installs `make` and `python3` from the signed Ubuntu archive, only to bootstrap the installer. Both
are signed distribution packages, nothing that ships or that CI runs comes from them, and every tool the checks use
is still the pinned one.

kind gets its Docker daemon from the Docker-in-Docker feature: a daemon of its own inside the container, so clusters
and images stay out of the host's daemon. The trade-off is that the container runs **privileged** (the feature asks
for it), which gives it the host kernel's devices and capabilities; mounting the host's Docker socket instead
(Docker-outside-of-Docker) needs no privilege but hands the container root-equivalent control of the host daemon, and
kind's API server then listens on the host, not in the container. Use the container on a machine you would run
privileged containers on, or install the tools with `make tools` directly.

---

## Clone & Initial Setup

```bash
# Clone the repository
git clone <YOUR_REPO_URL>
cd <YOUR_REPO_DIR>

# Copy the sample environment file
cp .env.example .env
```

Install the git hook once (`make precommit` runs every hook on all files):

```bash
scripts/tools.sh install prek
.tools/bin/prek install
```

---

## Adapt the Service Name

The template ships with a placeholder name. Rename it before first use: [Adaptation](adaptation.md) lists every form
the adapter replaces.

### Rename the Service

```bash
# Dry-run first (shows what would change without modifying files)
infra/adapt/replace_service_name.sh -n awesome_solution --dry-run

# Apply the rename
infra/adapt/replace_service_name.sh -n awesome_solution
```

The README badges (CI, OpenSSF Scorecard, licence, SLSA) and the private-reporting links in `SECURITY.md` and the issue forms use a placeholder repository, and `.github/CODEOWNERS` a placeholder owner. The images' OCI `source` label carries the repository too, for local and kind builds (CI passes the real URL). Set `REPOSITORY=owner/name`, and `CODEOWNER` (a user `@name` or a team `@org/team`) for `CODEOWNERS`, in `infra/adapt/adopt.conf` and run `infra/adapt/adopt.sh` to stamp all of them; see [Adaptation](adaptation.md#config-driven-adoption-kubernetes-deployments).

### Configure Ports (Optional)

Ports are environment-driven — there is no adaptation script. The defaults
(`BACKEND_PORT=8000`, `FRONTEND_PORT=3000`) come from `.env` / `.env.example`
and drive `docker-compose.yml` and the workflows' `env` blocks. In Kubernetes the container ports are fixed
and external access is handled by the Ingress (see
[Infrastructure](./infrastructure.md)).

> See [Adaptation Guide](./adaptation.md) for the service-rename script and the ports reference.

---

## Configure Environment

Edit `.env` to match your environment. Key variables:

```bash
# General
ENV=DEV
DEBUG=TRUE
LOG_LEVEL=INFO
API_VERSION=v1

# Backend
BACKEND_HOST=127.0.0.1
BACKEND_PORT=8000

# Frontend
FRONTEND_HOST=127.0.0.1
FRONTEND_PORT=3000
NEXT_PUBLIC_API_URL=http://127.0.0.1:${BACKEND_PORT}

# Database
DATABASE_URL=postgresql+asyncpg://${DB_USER:-app}:${DB_PASSWORD:-changeme}@localhost:5432/${DB_NAME:-service_template}
DB_USER=app
DB_PASSWORD=changeme
DB_NAME=service_template
```

> See [Environment Variables Reference](./environment-variables.md) for the complete list.

### Kubernetes-Specific Configuration

For Kubernetes deployments, environment variables are managed by the Helm chart's ConfigMap and
Secret, with per-environment values selected from `charts/your-service-name/values.<env>.yaml`. See
[Infrastructure](./infrastructure.md) for details.

---

## Backend Setup

### 1. Install uv

The backend is locked with [uv](https://docs.astral.sh/uv/). The pinned version is in `mise.toml`;
`scripts/tools.sh install uv` installs it (checksum-verified) into `.tools/bin`.

```bash
scripts/tools.sh install uv
export PATH="$PWD/.tools/bin:$PATH"
```

### 2. Install Dependencies

Pick one (`uv sync` is exact, so the second would remove what the first installed):

```bash
make develop            # development: uv sync --locked, the locked set plus the dev group
source .venv/bin/activate
```

For a runtime-only environment (no pytest, ruff or mypy), run `make install` instead
(`uv sync --locked --no-dev`).

Without uv, `pip install -e . --group dev` (pip 25.1 or newer) installs the same dependencies from
`pyproject.toml`, unlocked and without hashes.

### Lock file and the 7-day rule

`uv.lock` pins every dependency, with hashes. Change it only with these commands, and commit it with
the `pyproject.toml` change:

| Task | Command |
|---|---|
| Re-resolve after editing `pyproject.toml` | `make lock` (`uv lock`) |
| Fail if the lock is stale | `make lock-check` (`uv lock --check`; CI and the git hook run it) |
| Install exactly the lock | `make develop` (`uv sync --locked`) |
| Upgrade one package | `uv lock --upgrade-package <name>` |
| Upgrade the dev tools | `make dev-tools-upgrade` |
| See what is outdated | `make dev-tools-outdated` or `uv tree --outdated --depth 1` |

A locked release must be at least 7 days old, like the pinned tools. `[tool.uv] exclude-newer = "7 days"`
in `pyproject.toml` is a relative span: uv resolves it when you run `uv lock` and records
`exclude-newer-span = "P7D"` in the lock. The lock holds no clock reading, so `uv lock --check` answers
the same on any later day; only an explicit re-lock moves the cutoff. The container image builds from
the same lock with `uv sync --frozen --no-dev` (see [Containerization](./containerization.md)).

#### Security updates younger than 7 days

`exclude-newer` also hides a fix released less than 7 days ago, so a Dependabot security pull request for
it fails its lock check until you take this step (the Dependabot cooldown does not apply to security
updates, the lock's span does). To ship such a fix at once:

1. Exempt that one package, with a timestamp (never `false`, so the exemption stays bounded), in
   `pyproject.toml`: `[tool.uv] exclude-newer-package = { <package> = "<now, RFC 3339>" }`. The uv settings
   reference: "Accepts a dictionary format of `PACKAGE = "DATE"` pairs ... Set a package to `false` to
   exempt it from the global `exclude-newer` constraint entirely."
2. Run `uv lock --upgrade-package <package>` and commit `pyproject.toml` and `uv.lock`.
3. Remove the exemption at the next routine refresh (`make lock`), so the package falls back under the
   7-day rule.

The frontend's and the documentation site's lockfiles have the same rule, as `minimumReleaseAge: 10080` (7 days,
in minutes) in each project's `pnpm-workspace.yaml`; a frozen install checks every lockfile entry against it again.
For a fix younger than that, in that project:

1. Exempt that one exact version, with its publish date and the reason, in `pnpm-workspace.yaml`:

   ```yaml
   minimumReleaseAgeExclude:
     - <package>@<version>  # published <YYYY-MM-DD>; <advisory id>
   ```

   The pnpm settings reference: "You may also exempt specific versions (or a list of specific versions using a
   disjunction with ||). This allows pinning exceptions to mature-time rules."
2. Run `pnpm up <package>@<version>` and commit `pnpm-workspace.yaml`, `package.json` and `pnpm-lock.yaml`.
3. Remove the entry once the version is 7 days old: `tests/supply_chain/test_pnpm_config.py` fails from that day
   on, so no exemption outlives its purpose.

#### Build dependencies

The PEP 517 build backend (setuptools, setuptools-scm and their build dependencies) is pinned in
`[tool.uv] build-constraint-dependencies` with exact versions and sha256 hashes, which uv records under
`[manifest]` in `uv.lock` and checks when it builds the project (uv settings reference: "Hashes can be
included to verify downloaded build dependency archives ... uv records these hashes in uv.lock"). This
covers what the project's own build requests; build requirements of any future source-only dependency
are not hash-locked until they are added to that list. Update the list with the floor in
`[build-system] requires` (versions at least 7 days old): each entry's hashes are the sha256 of that version's files
on PyPI (its project page lists them per file), then run `make lock`. `setuptools-scm` and `vcs-versioning` are also
pinned in the dev group, and a test keeps both places equal; the notes of a Renovate pull request that moves them say
what to do when it changed only the dev group.

### 3. Initialize the Database

```bash
# Run the migrations (the app does not migrate at startup)
python -m your_service_name.backend.db.run_migrations
```

> If `DATABASE_URL` is not set, the backend falls back to SQLite (`sqlite+aiosqlite:///./data/items.db`).

### 4. (Optional) Seed Sample Data

```bash
python -m your_service_name.scripts.seed
```

---

## Frontend Setup

### 1. Install Dependencies

```bash
cd frontend
pnpm install --frozen-lockfile
```

The pinned pnpm comes with `make tools` (or `scripts/tools.sh install pnpm`); its store and cache live in `.tools/pnpm/`
(`pnpm-workspace.yaml`). pnpm also keeps a small state file for its update check; the Makefile and the git hooks
point it at `.tools/pnpm/state` too (`XDG_STATE_HOME`), and a bare `pnpm` call writes it under
`~/.local/state/pnpm` unless you export the same.

### 2. Generate API Types

```bash
# Generates TypeScript types from the backend OpenAPI spec
pnpm run openapi-gen
```

### 3. Start Development Server

```bash
pnpm run dev
# Frontend runs at http://127.0.0.1:3000
```

---

## Running Locally

Run each service in its own terminal. Two terminals minimum (backend + frontend):

### Terminal 1 — Backend

```bash
./run_backend.sh   # runs the migrations, then serves with auto-reload
# Backend API at http://127.0.0.1:8000
# API docs at http://127.0.0.1:8000/docs
```

### Terminal 2 — Frontend

```bash
cd frontend
pnpm run dev
# Frontend at http://127.0.0.1:3000
```

---

## Running with Docker Compose

The fastest way to run the full stack locally:

```bash
# Build all images
docker compose build

# Start all services (postgres, backend, frontend)
docker compose up -d

# View logs
docker compose logs -f

# Stop everything
docker compose down
```

### Services

| Service | Port | Health Check |
|---------|------|-------------|
| **postgres** | 5432 (internal) | `pg_isready` |
| **backend** | `${BACKEND_PORT}` | `/api/${API_VERSION}/healthz` |
| **frontend** | `${FRONTEND_PORT}` | `/api/healthz` |

### Access

- **Backend API**: `http://localhost:8000`
- **Frontend**: `http://localhost:3000`
- **API Docs**: `http://localhost:8000/docs`

---

## Running on Kubernetes (Local)

### The default: kind, as CI runs it

kind is the default local cluster ([ADR-0035](./adr/0035-kind-default-local-cluster.md)): `make kind-up`,
`make kind-deploy`, the kind tests and `make kind-down` run the same scripts and the same node images as CI's kind
job (`tools/kind-nodes.tsv`: the chart's `kubeVersion` floor and kind's newest), on a cluster named `fst` with its own
kubeconfig. After `make tools`, follow [Infrastructure, Local cluster (kind)](./infrastructure.md#local-cluster-kind),
which lists every target and what each test needs.

### By hand, on kind or minikube

The steps below deploy with your own cluster and commands; minikube is optional, and
[Local Deployment (minikube)](./local-development-minikube.md) covers it step by step.

#### 1. Create a Local Cluster

```bash
# Using kind
kind create cluster --name your-service-name

# Using minikube
minikube start --memory=4096 --cpus=2
```

#### 2. (Optional) Install an Ingress Controller

Ingress is only needed if you want to reach the service by host instead of via `kubectl
port-forward` (step 5). For a quick local run you can skip it. To use it, deploy the chart in
ingress mode with the controller's namespace as the NetworkPolicy peer:
`--set edge.mode=ingress --set-json 'edge.namespaceSelector={"kubernetes.io/metadata.name":"ingress-nginx"}'` (the edge modes: [Infrastructure, Edge](./infrastructure.md#edge)).

```bash
helm install ingress-nginx ingress-nginx \
  --repo https://kubernetes.github.io/ingress-nginx \
  --namespace ingress-nginx --create-namespace
```

#### 3. Build the Images into the Node Daemon

The `local` values read bare `:local` image tags with `imagePullPolicy: IfNotPresent`, so the images
must already live on the node — build them there (no registry push).

```bash
# minikube: point Docker at the node daemon, then build the :local images
eval $(minikube docker-env)
make compose-build-backend compose-build-frontend

# kind: build into the host daemon, then load each :local image into the cluster
make compose-build-backend compose-build-frontend
kind load docker-image your-service-name-backend:local --name your-service-name
kind load docker-image your-service-name-frontend:local --name your-service-name
```

#### 4. Deploy with Helm

> The stack is deployed with the Helm chart (`charts/your-service-name/`). Namespaces are per-env
> (`your-service-name-local` app + `your-service-name-local-db` db); the
> [admin bootstrap](./infrastructure.md#namespace-bootstrap-one-time-admin-step) creates both (`k8s-env.sh apply` applies it). For
> `local`, images are read from the node daemon (bare `:local` tags) and secrets come from
> `values.local.yaml` — nothing is created by hand.

```bash
# Deploy + verify the whole stack in one command (recommended)
./scripts/e2e.sh --env local

# Or the raw calls: the admin bootstrap (the namespaces; wiki/infrastructure.md), then Helm
scripts/k8s-bootstrap.sh local | kubectl apply -f -
helm upgrade --install your-service-name charts/your-service-name --server-side=true --force-conflicts \
  -f charts/your-service-name/values.local.yaml \
  -n your-service-name-local
```

#### 5. Access the Service

```bash
# Port-forward to frontend
kubectl -n your-service-name-local port-forward svc/your-service-name-frontend 3000:3000

# Or access via Ingress (if DNS is configured)
# http://your-service-name-dev.your-domain.example
```

#### 6. Verify Deployment

```bash
# Check pods are running
kubectl -n your-service-name-local get pods

# Check services
kubectl -n your-service-name-local get svc

# Check ingress
kubectl -n your-service-name-local get ingress

# View logs
kubectl -n your-service-name-local logs -l app.kubernetes.io/name=your-service-name -f
```

---

## Deploying to Kubernetes (Production)

### 1. Build and Push Images

The CI pipeline builds and pushes every image in [`.github/container-images.yml`](../.github/container-images.yml)
(buildx; the workflow map is in [CI/CD](./ci-cd.md#workflow-map)); each carries an immutable per-commit tag, and
production runs each image by digest (`images.<name>.digest`, set at deploy time).

To build manually for a private registry, tag with `:latest` so the push step resolves the
image you just built:

```bash
docker build -t <registry>/your-service-name-backend:latest  -f Dockerfile.backend  .
docker build -t <registry>/your-service-name-frontend:latest -f Dockerfile.frontend .

docker push <registry>/your-service-name-backend:latest
docker push <registry>/your-service-name-frontend:latest
```

### 2. Configure Secrets

dev, qa and production take the app Secret from the External Secrets Operator: set up the operator, the stores and
the secrets as in [GKE Cluster Setup, External Secrets](./gke-cluster-setup.md#14-the-apps-secrets-external-secrets-with-secret-manager),
then name the store in each `values.<env>.yaml`. The other sources are in [Security, Secret sources](./security.md#secret-sources).

### 3. Deploy to Production

```bash
# Once, as a cluster admin: the namespaces, quota and deployer RBAC (wiki/infrastructure.md)
scripts/k8s-bootstrap.sh production | kubectl apply -f -
# Deploy with Helm by digest. values.production.yaml holds placeholder digests, which the guard refuses;
# with waf.enabled, also pass --set images.waf.digest=sha256:<waf digest>.
helm upgrade --install your-service-name charts/your-service-name --server-side=true --force-conflicts \
  -f charts/your-service-name/values.production.yaml \
  --namespace your-service-name-production --wait --wait-for-jobs \
  --set guards.rejectPlaceholders=true \
  --set images.backend.digest=sha256:<backend digest> --set images.frontend.digest=sha256:<frontend digest>
```

<a id="production-needs-an-auth-mode"></a>
> **Production needs an auth mode.** [`values.production.yaml`](../charts/your-service-name/values.production.yaml)
> ships `AUTH_MODE: oidc` with a placeholder issuer and audience, which the placeholder guard
> (`guards.rejectPlaceholders=true`, set by the CI deploy and in the command above) refuses, so the render fails before
> any Pod exists. Set your provider's issuer and the API's audience, or another production auth
> mode ([Authentication](./security.md#authentication--authorization)), in that file before the first production
> deploy.

### 4. Verify Rollout

```bash
# Wait for backend rollout
kubectl -n your-service-name-production rollout status deployment/your-service-name-backend

# Wait for frontend rollout
kubectl -n your-service-name-production rollout status deployment/your-service-name-frontend

# Check HPA
kubectl -n your-service-name-production get hpa

# Check PDB
kubectl -n your-service-name-production get pdb
```

### 5. Run Database Migrations

Migrations run automatically as a Kubernetes Job on each deployment (one per revision; see [Database operations](./database.md#the-migration-job)). Verify:

```bash
kubectl -n your-service-name-production get jobs
kubectl -n your-service-name-production logs job/<release>-your-service-name-migrate-r<revision>
```

---

## Verify Everything Works

### Backend Health

```bash
curl -fsS http://127.0.0.1:${BACKEND_PORT}/api/${API_VERSION}/health
# Expected: {"ok": true} or {"ok": false, "detail": "..."}
```

### Frontend

```bash
curl -fsS http://127.0.0.1:${FRONTEND_PORT}/
# Expected: HTML response
```

### Kubernetes Health (via Ingress)

```bash
# Backend health via Ingress
curl -fsS https://your-service-name.your-domain.example/api/v1/health

# Frontend via Ingress
curl -fsS https://your-service-name.your-domain.example/

# Healthz (always returns ok; the edge routes /api to the backend)
curl -fsS https://your-service-name.your-domain.example/api/v1/healthz
```

### API Docs

Open in browser: `http://127.0.0.1:${BACKEND_PORT}/docs`

Or via Ingress: `https://your-service-name.your-domain.example/docs`

### Items CRUD

```bash
# Create an item
curl -X POST http://127.0.0.1:${BACKEND_PORT}/api/v1/items \
  -H "Content-Type: application/json" \
  -d '{"name": "Test Item", "description": "A test", "is_active": true}'

# List items
curl http://127.0.0.1:${BACKEND_PORT}/api/v1/items

# Get one item
curl http://127.0.0.1:${BACKEND_PORT}/api/v1/items/<id>

# Update an item
curl -X PATCH http://127.0.0.1:${BACKEND_PORT}/api/v1/items/<id> \
  -H "Content-Type: application/json" \
  -d '{"name": "Updated Item"}'

# Delete an item
curl -X DELETE http://127.0.0.1:${BACKEND_PORT}/api/v1/items/<id>
```

---

## Next Steps

- Read the [Developer Guide](./developer-guide.md) for Makefile commands and code quality tools.
- Review the [Architecture](./architecture.md) to understand the system design.
- Check [Testing Strategy](./testing.md) to run the test suite.
- Read the [Operations Runbook](./operations-runbook.md) for Kubernetes operational procedures.
- Review the [Infrastructure](./infrastructure.md) guide for Kubernetes resource details.
