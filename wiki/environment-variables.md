# Environment Variables

Complete reference for all environment variables used by the service template.

---

## Table of Contents

- [Overview](#overview)
- [General](#general)
- [Backend](#backend)
- [Frontend](#frontend)
- [Nginx](#nginx)
- [Runtime Mode (Local Dev Only)](#runtime-mode-local-dev-only)
- [TLS](#tls)
- [Database](#database)
- [Logging](#logging)
- [Rate Limiting](#rate-limiting)
- [CI/CD](#cicd)
- [Kubernetes-Specific](#kubernetes-specific)

---

## Overview

All configuration is done via **environment variables**. No hard-coded values. The `.env.example` file provides defaults.

```bash
# Copy and edit
cp .env.example .env
```

### Configuration Sources

| Source | Purpose | Priority |
|--------|---------|----------|
| `.env` file | Local development | Lowest |
| Docker Compose | Container orchestration | Medium |
| Kubernetes ConfigMap | Cluster configuration | High |
| Kubernetes Secret | Sensitive data | Highest |

---

## General

| Variable | Default | Description |
|----------|---------|-------------|
| `ENV` | `dev` | Deployment environment (`dev`, `qa`, `production`) |
| `DEBUG` | `false` | Enable debug mode (verbose logging, detailed errors) |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `LOG_FORMAT` | _(unset)_ | Emit structured JSON logs when `json` (also forced on in `production`). See [Log correlation & tracing](./operations-runbook.md#log-correlation--tracing). |
| `API_VERSION` | `v1` | Active API version (must be in `SUPPORTED_VERSIONS`) |

---

## Backend

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKEND_ENTRY` | `your_service_name.backend.main:app` | ASGI entry point for Gunicorn |
| `BACKEND_HOST` | `127.0.0.1` | Backend bind host |
| `BACKEND_PORT` | `8000` | Backend bind port |
| `BACKEND_URL` | `http://localhost:${BACKEND_PORT}` | Full backend URL |
| `BACKEND_BIND` | — | Override bind address (set to `0.0.0.0` in containers) |
| `GUNICORN_WORKERS` | `1` | Number of Gunicorn worker processes (1 per pod; Kubernetes scales pods, and a single readiness probe must observe the only worker) |
| `GUNICORN_TIMEOUT` | `120` | Gunicorn worker timeout (seconds) |
| `GUNICORN_RELOAD` | `false` | Auto-reload on code changes (development only) |
| `CUDA_VISIBLE_DEVICES` | _(empty)_ | GPU visibility (leave empty if not using GPU) |

---

## Frontend

| Variable | Default | Description |
|----------|---------|-------------|
| `FRONTEND_IMPL` | `react` | Frontend implementation (`react`) |
| `FRONTEND_HOST` | `127.0.0.1` | Frontend bind host |
| `FRONTEND_PORT` | `3000` | Frontend bind port |
| `FRONTEND_BIND` | — | Override bind address (set to `0.0.0.0` in containers) |
| `FRONTEND_REACT_SERVER` | `./frontend/.next/standalone/server.js` | Next.js standalone server path |
| `FRONTEND_ROOT` | `./frontend` (local) / `/app/frontend` (container) | Frontend build root directory |
| `NEXT_PUBLIC_API_URL` | `/api/v1` | API base URL (exposed to browser; same-origin, proxied by the Next.js server) |

---

## Nginx

| Variable | Default | Description |
|----------|---------|-------------|
| `NGINX_HOST` | `127.0.0.1` | Nginx bind host |
| `NGINX_PORT` | `8081` | Nginx bind port |
| `NGINX_BIND` | — | Override bind address (set to `0.0.0.0` in containers) |
| `NGINX_CONF` | `./infra/nginx/nginx.conf` | Nginx configuration file path |

---

## Runtime Mode (Local Dev Only)

> **Note**: No reverse proxy is used. The NGINX Ingress Controller serves as the edge gateway in Kubernetes. For local development, `infra/runtime/entrypoint.sh` can start the backend, frontend and nginx together (`--mode all`) or individually (`--mode backend` / `frontend` / `nginx`).

> `SERVICE_MODE` (valid: `backend` \| `frontend` \| `nginx` \| `all`, default `backend`) is **no longer used** in production, where each container runs one service directly (gunicorn for backend, node for frontend, nginx for the edge proxy). Locally it selects which services `infra/runtime/entrypoint.sh` starts.

---

## TLS

| Variable | Default | Description |
|----------|---------|-------------|
| `TLS_CERT_PEM` | _(empty)_ | TLS certificate in PEM format (enables HTTPS) |
| `EXTRA_CA_PEM` | _(empty)_ | Custom root CA certificate for internal PKI |

> When `TLS_CERT_PEM` is empty, the service runs in plain HTTP mode. TLS termination is handled by the NGINX Ingress Controller in production.

---

## Database

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@localhost:5432/${DB_NAME}` | Database connection URL |
| `DB_USER` | `app` | PostgreSQL username |
| `DB_PASSWORD` | `changeme` | PostgreSQL password |
| `DB_NAME` | `service_template` | PostgreSQL database name |

### SQLite Fallback

If `DATABASE_URL` is **not set**, the backend falls back to SQLite:

```
sqlite+aiosqlite:///./data/items.db
```

---

## Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_DIR` | `./logs` | Directory for log files |
| `LOG_REQUESTS` | `false` | Log every HTTP request (method, path, status, duration) |

---

## Rate Limiting

| Variable | Default | Description |
|----------|---------|-------------|
| `RATE_LIMIT_DEFAULT` | `100/minute` | Per-IP rate limit (`limits` format: `N/minute`, `N/second`) |

---

## CI/CD

The workflows read GitHub's built-in context; do not set these by hand. `scripts/ci/*` and `.github/scripts/container_matrix.py` use the environment variables:

| Variable | Source | Description |
|----------|--------|-------------|
| `GITHUB_REF_TYPE` | GitHub Actions | `tag` or `branch` |
| `GITHUB_REF_NAME` | GitHub Actions | The tag or branch name |
| `GITHUB_SHA` | GitHub Actions | Full commit SHA (the image tag uses the first 7 characters) |
| `GITHUB_EVENT_NAME` | GitHub Actions | Trigger event (`pull_request`, `push`, ...) |
| `GITHUB_REPOSITORY` / `GITHUB_REPOSITORY_OWNER` | GitHub Actions | Repository and owner, for the `ghcr.io/<owner>/<repo>` image paths |
| `GITHUB_TOKEN` | GitHub Actions | Short-lived token; the `release` workflow uses it to push to `ghcr.io` (`packages: write`) |
| `DEPLOY_ENV` | the deploy job | Target environment (`dev`, `qa`, `production`) |

Repository and environment variables that configure the deploy (`DEPLOY_ENABLED`, `DEPLOY_ENVS`, `SERVICE_NAME`, `HELM_TIMEOUT`, `GKE_CLUSTER`, ...) are listed in [Platform Setup](./platform-setup.md#variables-and-secrets).

---

## Kubernetes-Specific

These variables are managed via Kubernetes ConfigMap and Secret resources:

### ConfigMap Variables

| Variable | Purpose | Set In |
|----------|---------|--------|
| `ENV` | Deployment environment | `values.<env>.yaml` |
| `DEBUG` | Debug mode | `values.<env>.yaml` |
| `LOG_LEVEL` | Logging level | `values.<env>.yaml` |
| `API_VERSION` | API version | `values.<env>.yaml` |
| `BACKEND_HOST` | Backend bind host | chart `values.yaml` |
| `BACKEND_PORT` | Backend bind port | chart `values.yaml` |
| `FRONTEND_HOST` | Frontend bind host | chart `values.yaml` |
| `FRONTEND_PORT` | Frontend bind port | chart `values.yaml` |
| `NEXT_PUBLIC_API_URL` | Frontend API URL | `values.<env>.yaml` |
| `RATE_LIMIT_DEFAULT` | Rate limit | chart `values.yaml` |
| `LOG_REQUESTS` | Request logging | chart `values.yaml` |

### Secret Variables

| Variable | Purpose | Set In |
|----------|---------|--------|
| `DB_PASSWORD` | Database password | Sealed secret / External secret |
| `DB_USER` | Database username | Sealed secret / External secret |
| `DB_NAME` | Database name | Sealed secret / External secret |

### Per-environment configuration (`values.<env>.yaml`)

> The pre-Helm Kustomize overlays (`k8s/overlays/<env>`) are gone — per-env config now lives in
> `charts/your-service-name/values.<env>.yaml`, applied with `helm upgrade --install -f
> charts/your-service-name/values.<env>.yaml` (the `k8s/` tree was removed in Phase 2 Stage C). The
> non-secret settings below live under `config:` (rendered into the ConfigMap); see
> [`values.yaml`](../charts/your-service-name/values.yaml) for the full schema.

**Dev** (`charts/your-service-name/values.dev.yaml`):

```yaml
environment: dev
config:
  ENV: development
  DEBUG: "true"
  LOG_LEVEL: DEBUG
  NEXT_PUBLIC_API_URL: "/api/v1"
```

**Production** (`charts/your-service-name/values.production.yaml`):

```yaml
environment: production
config:
  ENV: production
  DEBUG: "false"
  LOG_LEVEL: WARNING
  NEXT_PUBLIC_API_URL: "/api/v1"
```

---

## Quick Reference Card

For local development, copy the single-source [`.env.example`](../.env.example) and fill in the
values — it is the one place these defaults live, so they are not restated (and cannot drift) here:

```bash
cp .env.example .env
```

The only required value is `DB_PASSWORD` — a missing or empty value makes `docker compose` fail fast
with a clear message. Everything else has a working default.
