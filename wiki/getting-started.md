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
| **Node.js** | 22 | Frontend runtime |
| **npm** | 11 | Frontend package manager |
| **Docker** or **Podman** | Latest | Container runtime |
| **kubectl** | 1.28+ | Kubernetes CLI |
| **Helm** | 3.0+ | Deploy the chart (`helm upgrade --install`) |
| **make** | — | Development shortcuts (optional) |

### For Kubernetes Deployment

| Tool | Purpose |
|------|---------|
| **kind** or **minikube** | Local Kubernetes cluster for development |
| **Helm** (optional) | Package manager for cluster-level dependencies (cert-manager, ingress-nginx) |

### Optional

- **Apptainer** — For Singularity-style (rootless, single-file) container runs
- **WSL2** — Required on Windows

---

## Clone & Initial Setup

```bash
# Clone the repository
git clone <YOUR_REPO_URL>
cd <YOUR_REPO_DIR>

# Copy the sample environment file
cp .env.example .env
```

Install the pre-commit hook once (`make precommit` runs every hook on all files):

```bash
scripts/tools.sh install pre-commit
.tools/bin/pre-commit install
```

---

## Adapt the Service Name

The template ships with a placeholder name (`your_service_name`). Rename it before first use.

### Rename the Service

```bash
# Dry-run first (shows what would change without modifying files)
infra/adapt/replace_service_name.sh -n awesome_solution --dry-run

# Apply the rename
infra/adapt/replace_service_name.sh -n awesome_solution
```

The README badges (CI and OpenSSF Scorecard) use `<owner>/<your-service-name>`; replace `<owner>` with your GitHub organisation or user, and the repository name if it differs from the service name.

### Configure Ports (Optional)

Ports are environment-driven — there is no adaptation script. The defaults
(`BACKEND_PORT=8000`, `FRONTEND_PORT=3000`, `NGINX_PORT=8081`) come from `.env` / `.env.example`
and drive `docker-compose.yml` and the workflows' `env` blocks. In Kubernetes the container ports are fixed
and external access is handled by the NGINX Ingress Controller (see
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

The backend is locked with [uv](https://docs.astral.sh/uv/). The pinned version is in `tools/pins.tsv`;
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
| Fail if the lock is stale | `make lock-check` (`uv lock --check`; CI and pre-commit run it) |
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

#### Build dependencies

The PEP 517 build backend (setuptools, setuptools-scm and their build dependencies) is pinned in
`[tool.uv] build-constraint-dependencies` with exact versions and sha256 hashes, which uv records under
`[manifest]` in `uv.lock` and checks when it builds the project (uv settings reference: "Hashes can be
included to verify downloaded build dependency archives ... uv records these hashes in uv.lock"). This
covers what the project's own build requests; build requirements of any future source-only dependency
are not hash-locked until they are added to that list. Update the list with the floor in
`[build-system] requires` (versions at least 7 days old), then run `make lock`.

### 3. Initialize the Database

```bash
# Run Alembic migrations
alembic upgrade head
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
npm ci
```

### 2. Generate API Types

```bash
# Generates TypeScript types from the backend OpenAPI spec
npm run openapi-gen
```

### 3. Start Development Server

```bash
npm run dev
# Frontend runs at http://127.0.0.1:3000
```

---

## Running Locally

Run each service in its own terminal. Two terminals minimum (backend + frontend):

### Terminal 1 — Backend

```bash
source .venv/bin/activate
python -m your_service_name.backend.main
# Backend API at http://127.0.0.1:8000
# API docs at http://127.0.0.1:8000/docs
```

### Terminal 2 — Frontend

```bash
cd frontend
npm run dev
# Frontend at http://127.0.0.1:3000
```

---

## Running with Docker Compose

The fastest way to run the full stack locally:

```bash
# Build all images
docker compose build

# Start all services (postgres, backend, frontend, nginx)
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
| **backend** | `${BACKEND_PORT}` | `/api/${API_VERSION}/health` |
| **frontend** | `${FRONTEND_PORT}` | `GET /` |
| **nginx** | `${NGINX_PORT}` | `/healthz` |

### Access

- **Backend API**: `http://localhost:8000`
- **Frontend**: `http://localhost:3000`
- **Nginx (static)**: `http://localhost:8081`
- **API Docs**: `http://localhost:8000/docs`

---

## Running on Kubernetes (Local)

For local Kubernetes development, use [kind](https://kind.sigs.k8s.io/) or [minikube](https://minikube.sigs.k8s.io/).

### 1. Create a Local Cluster

```bash
# Using kind
kind create cluster --name your-service-name

# Using minikube
minikube start --memory=4096 --cpus=2
```

### 2. (Optional) Install an Ingress Controller

Ingress is only needed if you want to reach the service by host instead of via `kubectl
port-forward` (step 5). For a quick local run you can skip it.

```bash
helm install ingress-nginx ingress-nginx \
  --repo https://kubernetes.github.io/ingress-nginx \
  --namespace ingress-nginx --create-namespace
```

### 3. Build the Images into the Node Daemon

The `local` values read bare `:local` image tags with `imagePullPolicy: IfNotPresent`, so the images
must already live on the node — build them there (no registry push).

```bash
# minikube: point Docker at the node daemon, then build the :local images
eval $(minikube docker-env)
make compose-build-backend compose-build-frontend compose-build-nginx

# kind: build into the host daemon, then load each :local image into the cluster
make compose-build-backend compose-build-frontend compose-build-nginx
kind load docker-image your-service-name-backend:local --name your-service-name
kind load docker-image your-service-name-frontend:local --name your-service-name
kind load docker-image your-service-name-nginx:local --name your-service-name
```

### 4. Deploy with Helm

> The stack is deployed with the Helm chart (`charts/your-service-name/`). Namespaces are per-env
> (`your-service-name-local` app + `your-service-name-local-db` db); the chart creates both. For
> `local`, images are read from the node daemon (bare `:local` tags) and secrets come from
> `values.local.yaml` — nothing is created by hand.

```bash
# Deploy + verify the whole stack in one command (recommended)
./scripts/e2e.sh --env local

# Or the raw Helm call
helm upgrade --install your-service-name charts/your-service-name \
  -f charts/your-service-name/values.local.yaml \
  -n your-service-name-local --create-namespace
```

### 5. Access the Service

```bash
# Port-forward to frontend
kubectl -n your-service-name-local port-forward svc/your-service-name-frontend 3000:3000

# Or access via Ingress (if DNS is configured)
# http://dev-api.your-domain.example
# http://dev-app.your-domain.example
```

### 6. Verify Deployment

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

The CI pipeline builds and pushes all three images (buildx — see
[`containerization.md`](containerization.md)); each carries an immutable per-commit tag and
production pins the digest (`images.backend.digest` in `values.production.yaml`).

To build manually for a private registry, tag with `:latest` so the push step resolves the
image you just built:

```bash
docker build -t <registry>/your-service-name-backend:latest  -f Dockerfile.backend  .
docker build -t <registry>/your-service-name-frontend:latest -f Dockerfile.frontend .
docker build -t <registry>/your-service-name-nginx:latest    -f Dockerfile.nginx    .

docker push <registry>/your-service-name-backend:latest
docker push <registry>/your-service-name-frontend:latest
docker push <registry>/your-service-name-nginx:latest
```

### 2. Configure Secrets

Create sealed-secrets or configure External Secrets Operator for production secrets:

```bash
# Production leaves secrets empty in values.production.yaml; supply them via sealed-secrets / ESO / SOPS.
# Example: seal DB credentials into the production app namespace
kubeseal --format yaml < db-credentials.json | kubectl apply -f - -n your-service-name-production
```

### 3. Deploy to Production

```bash
# Deploy with Helm (image ref comes from values.production.yaml; CI sets the immutable tag/digest)
helm upgrade --install your-service-name charts/your-service-name \
  -f charts/your-service-name/values.production.yaml \
  -n your-service-name-production --create-namespace
```

### 4. Verify Rollout

```bash
# Wait for backend rollout
kubectl -n your-service-name rollout status deployment/your-service-name-backend

# Wait for frontend rollout
kubectl -n your-service-name rollout status deployment/your-service-name-frontend

# Check HPA
kubectl -n your-service-name get hpa

# Check PDB
kubectl -n your-service-name get pdb
```

### 5. Run Database Migrations

Migrations run automatically as a Kubernetes Job on each deployment. Verify:

```bash
kubectl -n your-service-name get jobs
kubectl -n your-service-name logs job/your-service-name-migration
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
curl -fsS https://api.your-domain.example/api/v1/health

# Frontend via Ingress
curl -fsS https://app.your-domain.example/

# Healthz (always returns ok)
curl -fsS https://api.your-domain.example/healthz
```

### API Docs

Open in browser: `http://127.0.0.1:${BACKEND_PORT}/docs`

Or via Ingress: `https://api.your-domain.example/docs`

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
