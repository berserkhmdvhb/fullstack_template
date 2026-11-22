# Infrastructure

Kubernetes resources, the Ingress, Docker Compose for local development, and the entrypoint system.

---

## Table of Contents

- [Overview](#overview)
- [Kubernetes Resources](#kubernetes-resources)
- [Ingress](#ingress)
- [Static assets](#static-assets)
- [Entrypoint System](#entrypoint-system)
- [Docker Compose (Local Dev)](#docker-compose-local-dev)
- [Health Probes](#health-probes)
- [Configuration Reference](#configuration-reference)

---

## Overview

The infrastructure layer consists of Kubernetes resources that manage the application lifecycle:

```mermaid
flowchart TB
    subgraph Public["Public Facing"]
        IC["Ingress controller<br/>TLS termination<br/>Rate limiting"]
    end

    subgraph Internal["Internal Services"]
        BE["Backend<br/>FastAPI<br/>Port: 8000"]
        FE["Frontend<br/>Next.js, pages and /_next/static<br/>Port: 3000"]
    end

    subgraph Data["Data"]
        PG["PostgreSQL<br/>Port: 5432"]
    end

    User["User"] -->|"HTTPS"| IC
    IC -->|"everything else"| FE
    IC -->|"/api, /docs, /openapi.json"| BE
    FE -->|"API calls"| BE
    BE --> PG
```

---

## Kubernetes Resources

> **Deployed with the Helm chart** `charts/your-service-name/` (deploy pipeline in
> [`wiki/ci-cd.md`](./ci-cd.md)). The operational
> surface (`Makefile`, `scripts/`, `ci/deploy.yml`) drives `helm upgrade --install`; namespaces are
> per-env (`your-service-name-<env>` app + `<app-ns>-db` db). The resource set below maps 1:1 into the
> chart's `templates/`; the pre-Helm `k8s/` Kustomize tree was **removed in Phase 2 Stage C** (recoverable
> from git history, last touched `f72fdd1`) and is no longer part of the deploy flow.

### Chart templates (`charts/your-service-name/templates/`)

The base resource set maps 1:1 into the chart. Per-env differences live in
`values.<env>.yaml` (replicas, image tags/registry, hosts, gated Postgres), not in separate overlays.

---

## Ingress

The Ingress is the edge. It sends `/api`, `/docs` and `/openapi.json` to the backend (:8000) and everything else to the frontend (:3000), which serves its own pages and `/_next/static` assets (see [Static assets](#static-assets)). The chart's `values.yaml` holds the controller class and annotations.

### Security Headers

The Ingress adds no response headers. The backend's security middleware and the frontend's `next.config.ts` and `proxy.ts` set them; see [Security](./security.md#http-security-headers).

### Rate Limiting

```yaml
metadata:
  annotations:
    nginx.ingress.kubernetes.io/limit-rps: "100"
    nginx.ingress.kubernetes.io/limit-connections: "50"
```

### TLS Configuration

TLS is managed by cert-manager with Let's Encrypt:

```yaml
spec:
  tls:
    - hosts:
        - api.your-domain.example
        - app.your-domain.example
      secretName: your-service-name-tls
```

---

## Static assets

The frontend server serves its own static assets, and a CDN in front caches them at the edge. [`frontend/static-paths.ts`](../frontend/static-paths.ts) is the one list of static paths. The cache policy: [Frontend Deployment](./frontend-deployment.md#static-assets).

---

## Entrypoint System

Each service has its own Dockerfile with a dedicated `CMD` that starts the service directly. There is no `SERVICE_MODE` variable in production.

The `infra/runtime/entrypoint.sh` script is retained **only for local development convenience**. It supports a `--mode` flag to start individual services or all at once:

```bash
# Start a single service locally
./infra/runtime/entrypoint.sh --mode backend
./infra/runtime/entrypoint.sh --mode frontend

# Start backend and frontend together (local dev only)
./infra/runtime/entrypoint.sh --mode all
```

### Backend Entry

- Runs the database migrations first (`python -m your_service_name.backend.db.run_migrations`; the app does not migrate itself)
- Starts Uvicorn (one process per container; Kubernetes scales pods)
- Binds to `${BACKEND_HOST}:${BACKEND_PORT}`

### Frontend Entry

- Starts Next.js standalone server (`node .next/standalone/server.js`)
- Binds to `${FRONTEND_HOST}:${FRONTEND_PORT}`

---

## Docker Compose (Local Dev)

The `docker-compose.yml` orchestrates the full stack for local development with three long-running services and a one-shot `migrate` job:

```mermaid
flowchart TB
    subgraph Compose["docker-compose.yml"]
        PG["postgres<br/>image: postgres:16-alpine<br/>health: pg_isready"]
        MG["migrate<br/>Dockerfile.backend<br/>one-shot, depends: postgres healthy"]
        BE["backend<br/>Dockerfile.backend<br/>depends: migrate completed<br/>health: /api/v1/healthz"]
        FE["frontend<br/>Dockerfile.frontend<br/>depends: backend healthy<br/>health: /api/healthz"]
    end

    PG -->|"healthy"| MG
    MG -->|"completed"| BE
    BE -->|"healthy"| FE
```

### Service Dependencies

| Service | Depends On | Condition |
|---------|-----------|-----------|
| migrate | postgres | `service_healthy` |
| backend | migrate | `service_completed_successfully` |
| frontend | backend | `service_healthy` |

### Local Access

Access services directly on their ports:

| Service | URL |
|---------|-----|
| **Backend API** | `http://localhost:8000` |
| **Frontend** | `http://localhost:3000` |
| **API Docs** | `http://localhost:8000/docs` |

### Volumes

| Volume | Purpose |
|--------|---------|
| `postgres_data` | Persistent PostgreSQL data |

---

## Health Probes

### Kubernetes Probes

| Probe | Backend | Frontend |
|-------|---------|----------|
| **startupProbe** | `/api/v1/healthz` (30×5s, failureThreshold: 30) | `/api/healthz` (20×5s, failureThreshold: 20) |
| **readinessProbe** | `/api/v1/health` (10s interval, 5s timeout) | `/api/healthz` (10s interval, 5s timeout) |
| **livenessProbe** | `/api/v1/healthz` (15s interval, 5s timeout) | `/api/healthz` (15s interval, 5s timeout) |

### Docker Compose Health Checks

| Service | Endpoint | Method | Success |
|---------|----------|--------|---------|
| **postgres** | `pg_isready` | CMD-SHELL | Exit 0 |
| **backend** | `/api/${API_VERSION}/healthz` | HTTP GET | 200 |
| **frontend** | `/api/healthz` | HTTP GET | 200 |

### Health Endpoint Behavior

- **`/health`** — Readiness: 200 when the database is reachable and the schema is current, 503 otherwise (see [Database](./database.md))
- **`/healthz`** — Process only: no database or schema check

---

## Configuration Reference

### Environment Variables (Infrastructure)

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKEND_HOST` | 127.0.0.1 | Backend bind host |
| `BACKEND_PORT` | 8000 | Backend bind port |
| `FRONTEND_HOST` | 127.0.0.1 | Frontend bind host |
| `FRONTEND_PORT` | 3000 | Frontend bind port |
| `FRONTEND_ROOT` | `./frontend` (local) / `/app/frontend` (container) | Frontend build root |

### Kubernetes-Specific Configuration

Kubernetes environment variables are managed via the chart's ConfigMap and Secret resources; per-env values live in `charts/your-service-name/values.<env>.yaml`.
