# Developer Guide

Local development workflow, Makefile commands, code quality tools, Kubernetes development, and debugging tips.

---

## Table of Contents

- [Development Workflow](#development-workflow)
- [Makefile Commands](#makefile-commands)
- [Code Quality Tools](#code-quality-tools)
- [Frontend Commands](#frontend-commands)
- [Kubernetes Development](#kubernetes-development)
- [Debugging](#debugging)
- [Adding a New Endpoint](#adding-a-new-endpoint)
- [Adding a New Feature Module](#adding-a-new-feature-module)

---

## Development Workflow

### Recommended Setup

1. **Backend terminal**: Run backend in watch/reload mode
2. **Frontend terminal**: Run Next.js dev server
3. **Kubernetes terminal** (optional): Local cluster for integration testing

### Hot Reload

- **Backend**: Set `GUNICORN_RELOAD=true` in `.env` for auto-reload on code changes
- **Frontend**: `npm run dev` includes hot module replacement (HMR) by default

---

## Makefile Commands

### Installation

| Command | Description |
|---------|-------------|
| `make install` | Install package in editable mode (production deps) |
| `make develop` | Install with all development dependencies |

### Code Quality

| Command | Description |
|---------|-------------|
| `make ruff-format` | Auto-format code with Ruff |
| `make ruff-format-check` | Check formatting (dry run, no changes) |
| `make ruff-lint` | Run Ruff linter |
| `make mypy-check` | Run MyPy strict type checker |
| `make check-all` | Full check: outdated tools + format + lint + mypy |
| `make check-all-dry` | Full check without auto-format (format-check) |

### Testing

| Command | Description |
|---------|-------------|
| `make test` | Run unit tests (pytest) |
| `make test-integration` | Run integration tests (`-m integration`) |
| `make test-e2e` | Run E2E tests (`-m e2e`) |
| `make test-live` | Run live tests against running stack (`-m live`) |
| `make test-file FILE=path/to/test.py` | Run a single test file |
| `make test-file-function FILE=path/to/test.py FUNC=test_name` | Run a specific test function |
| `make test-fast` | Re-run only last failed tests |

### Coverage

| Command | Description |
|---------|-------------|
| `make test-coverage` | Run tests with terminal coverage summary (≥85% required) |
| `make test-coverage-xml` | Generate XML coverage report (for CI) |
| `make test-cov-html` | Generate HTML coverage report and open in browser |
| `make test-coverage-rep` | Show full line-by-line coverage report |
| `make test-coverage-file FILE=path/to/file.py` | Show coverage for a specific file |

### Maintenance

| Command | Description |
|---------|-------------|
| `make dev-tools-list` | List development tools from pyproject.toml |
| `make dev-tools-outdated` | Check all dev tools for available updates |
| `make dev-tools-upgrade` | Upgrade all dev tools to latest versions |
| `make version-check-<package>` | Check if a specific package has updates |
| `make check-updates` | List all outdated installed packages |
| `make check-toml` | Validate pyproject.toml syntax |

### Clean

| Command | Description |
|---------|-------------|
| `make clean-build` | Remove build artifacts (dist/, build/, *.egg-info) |
| `make clean-logs` | Remove DEV log files |
| `make clean-cache` | Remove pytest, mypy, ruff caches |
| `make clean-coverage` | Remove coverage data |
| `make clean-pyc` | Remove .pyc and __pycache__ files |
| `make clean-all` | Remove all artifacts (build, logs, cache, coverage, pyc) |

### Build

| Command | Description |
|---------|-------------|
| `make build` | Build Python package for distribution |
| `make export-requirements` | Export requirements.txt from pyproject.toml |
| `make check-requirements-sync` | Verify requirements.txt matches pyproject.toml |

---

## Code Quality Tools

### Backend (Python)

| Tool | Purpose | Config |
|------|---------|--------|
| **Ruff** | Linting + formatting | `pyproject.toml` `[tool.ruff]` — ALL rules, line-length 100 |
| **MyPy** | Static type checking | `pyproject.toml` `[tool.mypy]` — strict mode |

### Frontend (TypeScript/React)

| Tool | Purpose | Config |
|------|---------|--------|
| **ESLint** | Linting | `eslint.config.mjs` — flat config, 0 warnings allowed |
| **Prettier** | Formatting | `prettier.config.cjs` |
| **TypeScript** | Type checking | `tsconfig.json` — strict, exactOptionalPropertyTypes |

---

## Frontend Commands

All frontend commands are run from the `frontend/` directory:

| Command | Description |
|---------|-------------|
| `npm run dev` | Start Next.js development server |
| `npm run build` | Production build (generates `.next/standalone`) |
| `npm run start` | Start production server |
| `npm run lint` | ESLint (0 warnings allowed) |
| `npm run lint-fix` | ESLint with auto-fix |
| `npm run typecheck` | TypeScript type check |
| `npm run test` | Run all Vitest tests (unit + component) |
| `npm run test:unit` | Run unit tests only |
| `npm run test:integration` | Run integration/component tests (with MSW) |
| `npm run test:coverage` | Run tests with coverage |
| `npm run test:e2e` | Run Playwright E2E tests |
| `npm run test:e2e:ui` | Run Playwright E2E tests with UI mode |
| `npm run format` | Prettier format all files |
| `npm run format-check` | Prettier check (dry run) |
| `npm run openapi-gen` | Generate TypeScript types from OpenAPI spec |

---

## Kubernetes Development

### Local Cluster Setup

```bash
# Create a kind cluster
kind create cluster --name your-service-name

# Install NGINX Ingress Controller
helm install ingress-nginx ingress-nginx \
  --repo https://kubernetes.github.io/ingress-nginx \
  --namespace ingress-nginx --create-namespace

# Install cert-manager (for TLS)
helm install cert-manager cert-manager \
  --repo https://charts.jetstack.io \
  --namespace cert-manager --create-namespace \
  --set installCRDs=true
```

### Build and Load Images

```bash
# Build the :local images into the node daemon (imagePullPolicy: IfNotPresent picks them up)
make compose-build-backend compose-build-frontend compose-build-nginx

# kind: load each :local image into the cluster (minikube builds straight into the node)
kind load docker-image your-service-name-backend:local --name your-service-name
kind load docker-image your-service-name-frontend:local --name your-service-name
kind load docker-image your-service-name-nginx:local --name your-service-name
```

### Deploy to Local Cluster

> Deployed with the Helm chart (`charts/your-service-name/`). Namespaces are per-env
> (`your-service-name-local` app + `your-service-name-local-db` db); the chart creates them. For
> `local`, images are read from the node daemon (bare `:local` tags) and secrets come from
> `values.local.yaml` — nothing is created by hand.

```bash
# Deploy + verify the whole stack (secrets → apply → wait → in-cluster checks) in one command.
./scripts/e2e.sh --env local                 # helm upgrade --install (via k8s-env.sh) then verify
./scripts/e2e.sh verify --env local          # verify only — read-only, safe to re-run

# Or the raw Helm call:
helm upgrade --install your-service-name charts/your-service-name \
  -f charts/your-service-name/values.local.yaml -n your-service-name-local --create-namespace
```

### Access the Service

```bash
# Port-forward to frontend
kubectl -n your-service-name-local port-forward svc/your-service-name-frontend 3000:3000

# Or access via Ingress (if DNS is configured)
# http://dev-api.your-domain.example
# http://dev-app.your-domain.example
```

### Iterative Development

For rapid iteration, rebuild and redeploy:

```bash
# 1. Make code changes
# 2. Rebuild affected images (bare :local tags — rebuild only what changed)
make compose-build-backend   # if backend changed
make compose-build-frontend  # if frontend changed
make compose-build-nginx     # if nginx changed

# 3. Reload into cluster (kind; minikube builds straight into the node)
kind load docker-image your-service-name-backend:local --name your-service-name
kind load docker-image your-service-name-frontend:local --name your-service-name

# 4. Trigger rollout
kubectl -n your-service-name-local rollout restart deployment/your-service-name-backend
kubectl -n your-service-name-local rollout restart deployment/your-service-name-frontend

# 5. Wait for rollout
kubectl -n your-service-name-local rollout status deployment/your-service-name-backend
```

### Debugging in Kubernetes

```bash
# Exec into backend pod
kubectl -n your-service-name-local exec -it <pod-name> -- bash

# View logs
kubectl -n your-service-name-local logs -f <pod-name>

# Port-forward to backend
kubectl -n your-service-name-local port-forward svc/your-service-name-backend 8000:8000
```

---

## Debugging

### Backend

```bash
# Enable debug logging
DEBUG=TRUE LOG_LEVEL=DEBUG python -m your_service_name.backend.main

# Run with auto-reload
GUNICORN_RELOAD=true python -m your_service_name.backend.main

# Check database connectivity
python -c "from your_service_name.backend.db.engine import get_session; print('OK')"

# Inspect API routes
python -c "from your_service_name.backend.main import create_app; app = create_app(); print([r.path for r in app.routes])"
```

### Frontend

```bash
# Start dev server with verbose output
npm run dev

# Check TypeScript errors
npm run typecheck

# Run a single test file
npx vitest run frontend/lib/config.test.ts

# Run tests in watch mode
npx vitest
```

### Kubernetes

```bash
# Check pod status
kubectl -n your-service-name-local get pods

# Describe pod (check events)
kubectl -n your-service-name-local describe pod <pod-name>

# Check logs
kubectl -n your-service-name-local logs <pod-name>

# Check previous container logs (if restarted)
kubectl -n your-service-name-local logs <pod-name> --previous

# Test backend health from within cluster
kubectl -n your-service-name-local exec <backend-pod> -- curl -fsS http://localhost:8000/api/v1/health
```

---

## Adding a New Endpoint

### Backend

1. **Create router** in `src/your_service_name/backend/api/routers/v1/`:

```python
# new_feature.py
from fastapi import APIRouter

router = APIRouter(prefix="/new-feature", tags=["New Feature"])

@router.get("/")
async def list_new_features():
    ...
```

2. **Register router** in `main.py`:

```python
from .api.routers.v1 import new_feature

app.include_router(new_feature.router, prefix=f"/api/{API_VERSION}")
```

3. **Add schemas** in `src/your_service_name/backend/schemas/`
4. **Add service** in `src/your_service_name/backend/services/`
5. **Add repository** in `src/your_service_name/backend/repositories/`
6. **Add tests** in `tests/`

### Frontend

1. **Create API endpoint** in `frontend/lib/api/endpoints.ts`
2. **Create React Query hook** in `frontend/lib/hooks/`
3. **Create page** in `frontend/app/your-feature/page.tsx`
4. **Create components** in `frontend/features/your-feature/components/`
5. **Add tests** for components, hooks, and API functions

---

## Adding a New Feature Module

Follow the existing `items` feature as a template:

```
frontend/features/your-feature/
├── components/
│   ├── YourFeatureList.tsx
│   └── YourFeatureForm.tsx
└── lib/
    └── schemas.ts          # Zod validation schemas
```

Coordinate with the page component to manage state between list and form, following the same pattern as `app/items/page.tsx`.
