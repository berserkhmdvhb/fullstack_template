# Environment Variables

Complete reference for all environment variables used by the service template.

---

## Table of Contents

- [Overview](#overview)
- [Backend settings](#backend-settings)
- [General](#general)
- [Backend](#backend)
- [Frontend](#frontend)
- [Nginx](#nginx)
- [Runtime Mode (Local Dev Only)](#runtime-mode-local-dev-only)
- [TLS](#tls)
- [Database](#database)
- [Logging](#logging)
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

<!-- BEGIN GENERATED: backend settings (scripts/gen_env_docs.py; edit Settings, not this) -->

## Backend settings

Every variable the Python backend reads is declared once, in `Settings`
(`src/your_service_name/backend/settings.py`), and validated at startup: an invalid value stops
the backend with one error naming each problem. Names are case-sensitive.

`ENV` selects the deployment environment. When it is `production` (or `prod`, case-insensitive) the
rules in the last column are enforced at startup, and a violation stops the backend. Any other value
leaves them off, and an unset `ENV` is `dev`. The chart's production values set `ENV=production`, so
the rules are on there.

`TRUSTED_PROXY_CIDRS` and `TRUST_CF_CONNECTING_IP` decide which forwarding headers the backend
believes when it resolves the client IP; the model is in [Client IP](security.md#client-ip).

| Variable | Type | Default | Description | Production rule |
|---|---|---|---|---|
| `ENV` | str | `dev` | Deployment environment. `production` (or `prod`) turns on the production checks below; any other value leaves them off. | — |
| `LOG_LEVEL` | str | `INFO` | Root log level (name or number); an unknown value falls back to INFO. | — |
| `LOG_FORMAT` | str | (empty) | `json` emits structured JSON logs; they are also on when ENV is production. | — |
| `LOG_REQUESTS` | bool | `false` | Log every HTTP request (method, path, status, duration). | — |
| `API_VERSION` | str | `v1` | Active API version; must be one of the supported versions. | — |
| `API_TITLE` | str | `Backend Title` | OpenAPI document title. | — |
| `API_DESCRIPTION` | str | `Minimal FastAPI backend` | OpenAPI document description. | — |
| `API_DOCS_URL` | str | (unset) | Path of the Swagger UI. Unset means `/docs` (off in production); an empty value turns it off. | Off unless ALLOW_PRODUCTION_DOCS=true; setting it to a path without that flag is refused. |
| `OPENAPI_URL` | str | (unset) | Path of the OpenAPI JSON. Unset means `/openapi.json` (off in production); an empty value turns it off. | Off unless ALLOW_PRODUCTION_DOCS=true; setting it to a path without that flag is refused. |
| `ALLOW_PRODUCTION_DOCS` | bool | `false` | Production only: allow the Swagger UI and the OpenAPI JSON to be served. | — |
| `CORS_ORIGINS` | comma-separated list | (empty) | Comma-separated allowed origins. Empty means same-origin only. | Each origin must be a bare `scheme://host[:port]` (no path, no trailing slash). With CORS_ALLOW_CREDENTIALS=true it must be non-empty and must not contain `*`. |
| `CORS_ALLOW_CREDENTIALS` | bool | `false` | Allow credentialed cross-origin requests. Outside production a `*` origin is dropped (with a warning) when this is on. | — |
| `CORS_ALLOW_METHODS` | comma-separated list | `GET,POST,PUT,PATCH,DELETE,OPTIONS` | Comma-separated allowed methods. | — |
| `CORS_ALLOW_HEADERS` | comma-separated list | `Authorization,Content-Type,X-Requested-With` | Comma-separated allowed request headers. | — |
| `RATE_LIMIT_DEFAULT` | str | `100/minute` | Per-IP rate limit in `limits` format, for example `100/minute`. | — |
| `MAX_REQUEST_BODY_BYTES` | int | `10485760` | Largest accepted request body in bytes; more is a 413. The default (10 MiB) is the chart's ingress `proxy-body-size`, so the edge and the service agree. | — |
| `ALLOWED_HOSTS` | comma-separated list | `*` | Comma-separated `Host` values the service answers (`*.example.com` matches subdomains, `*` anything). Anything else is a 400; the probe and metrics paths are exempt. The chart sets it from the ingress hosts and the backend Service names. | Must be set and must not contain `*`. |
| `TRUSTED_PROXY_CIDRS` | comma-separated list | (empty) | Comma-separated IPv4/IPv6 CIDRs of the proxies (the gateway and, in the Tunnel edge mode, cloudflared) whose forwarding headers are trusted. Empty trusts nothing: the TCP peer is the client. An invalid entry stops startup, and so does one with host bits set (`10.1.2.3/8`; write `10.0.0.0/8`). | — |
| `TRUST_CF_CONNECTING_IP` | bool | `false` | Trust `CF-Connecting-IP` from a peer in TRUSTED_PROXY_CIDRS. Set it only in the Cloudflare Tunnel edge mode: Cloudflare sends the header on traffic from its edge, so trust it only when cloudflared is the only way in. | Refused when TRUSTED_PROXY_CIDRS is empty. |
| `AUTH_MODE` | one of `none`, `gateway`, `bearer` | `none` | `none` (anonymous), `gateway` (trusted identity header) or `bearer` (HMAC-signed JWT). | `none` is refused unless ALLOW_ANONYMOUS=true. |
| `ALLOW_ANONYMOUS` | bool | `false` | Production only: explicitly accept AUTH_MODE=none (open endpoints). | — |
| `AUTH_USER_ID_HEADER` | str | `x-authenticated-user-id` | Gateway mode: header carrying the authenticated user id. | — |
| `AUTH_ROLES_HEADER` | str | `x-authenticated-user-roles` | Gateway mode: header carrying comma-separated roles. | — |
| `AUTH_JWT_SECRET` | secret | (secret) | Bearer mode: shared HMAC secret. Required when AUTH_MODE=bearer. | — |
| `AUTH_JWT_ALGORITHM` | str | `HS256` | Bearer mode: HMAC algorithm (`HS256`, `HS384` or `HS512`). | — |
| `AUTH_JWT_USER_ID_CLAIM` | str | `sub` | Bearer mode: token claim holding the user id. | — |
| `DATABASE_URL` | secret | (secret) | Async SQLAlchemy database URL. Unset falls back to SQLite (SQLITE_PATH). | Required, and not SQLite. |
| `TEST_DATABASE_URL` | secret | (secret) | Test-only: database URL that takes precedence over DATABASE_URL. | Refused when set: it is test-only and would take precedence. |
| `SQLITE_PATH` | str | `data/items.db` | SQLite fallback file, relative to the project root. | Ignored: SQLite is refused. |
| `DB_POOL_SIZE` | int | `10` | PostgreSQL connection pool size; a non-positive or invalid value falls back to the default. | — |
| `DB_MAX_OVERFLOW` | int | `20` | PostgreSQL pool overflow; a non-positive or invalid value falls back to the default. | — |

<!-- END GENERATED -->

---

## General

| Variable | Default | Description |
|----------|---------|-------------|
| `DEBUG` | `false` | Enable debug mode (verbose logging, detailed errors) |

---

## Backend

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKEND_ENTRY` | `your_service_name.backend.main:app` | ASGI entry point for Uvicorn |
| `BACKEND_HOST` | `127.0.0.1` | Backend bind host |
| `BACKEND_PORT` | `8000` | Backend bind port |
| `BACKEND_URL` | `http://localhost:${BACKEND_PORT}` | Full backend URL |
| `BACKEND_BIND` | — | Override bind address (set to `0.0.0.0` in containers) |
| `BACKEND_KEEPALIVE_TIMEOUT` | `75` | Launcher-level (`infra/backend/run_backend.sh`; the Python app never reads it): seconds Uvicorn keeps an idle connection open. Keep it longer than the gateway's upstream idle timeout (`waf.upstreamIdleTimeoutSeconds`, 60 in the chart), or the gateway can reuse a connection just closed and return a 502. The same holds for ingress-nginx's `upstream-keepalive-timeout` (default 60 s): never raise it past this value. |
| `BACKEND_GRACEFUL_TIMEOUT` | `30` | Launcher-level: seconds Uvicorn drains in-flight requests after SIGTERM before it exits. |
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

| Variable | Default | Description |
|----------|---------|-------------|
| `SERVICE_MODE` | `backend` | Which services `infra/runtime/entrypoint.sh` starts: `backend`, `frontend`, `nginx` or `all`. No longer used in production, where each container runs one service directly (uvicorn for backend, node for frontend, nginx for the edge proxy). |

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
| `DB_USER` | `app` | PostgreSQL username |
| `DB_PASSWORD` | `changeme` | PostgreSQL password |
| `DB_NAME` | `service_template` | PostgreSQL database name |

`DATABASE_URL`, `TEST_DATABASE_URL`, `SQLITE_PATH` and the pool settings are in [Backend settings](#backend-settings). With `DATABASE_URL` unset the backend falls back to SQLite, which `ENV=production` refuses.

---

## Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_DIR` | `./logs` | Directory for log files |

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
  # An auth mode is required in production, e.g. AUTH_MODE: gateway (see Backend settings)
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
