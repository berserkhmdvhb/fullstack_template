# Changelog

All notable changes to this project, following [Keep a Changelog](https://keepachangelog.com/) format.

---

## [Unreleased]

<!-- Future changes accumulate here. When cutting a release, rename this heading to the
     version + date and open a fresh [Unreleased] above it (see wiki/releasing.md). -->

### Changed
- **The CI moved from GitLab CI to GitHub Actions**. The workflows in `.github/workflows/` replace the old `ci/*.yml` includes and `.gitlab-ci.yml`; `ci/` and `wiki/gitlab-ci-findings.md` are removed, the two live scripts moved to `scripts/ci/`, and `wiki/ci-cd.md` is rewritten with a job map from every old GitLab job to its workflow job. "Merge request" and "MR" became "pull request" and "PR" in the docs and skills. The required check is `CI OK`; images are built, pushed and scanned only on `main` and `v*` tags. The `add-supply-chain-scanner` scaffolder now prints GitHub Actions wiring. The frontend `unit` job runs `npm run test:unit` and failed jobs upload their test reports for 7 days.
- **One-time re-baseline of the frozen `infra/security/*.sh` scripts**. `generate-vex.sh` now reads `GITHUB_SHA`, `GITHUB_REPOSITORY` and `GITHUB_SERVER_URL`; `scan-vulnerabilities.sh` reads `GITHUB_WORKSPACE`; no `CI_*` fallback remains. The frozen set is now eight scripts: `_helpers.sh`, `sbom-generate.sh`, `scan-vulnerabilities.sh`, `scan-licenses.sh`, `generate-vex.sh`, `check-ci-hardening.sh`, `tools-install.sh`, `verify-image-digest.sh`. `check-ci-hardening.sh` was retargeted (comments, usage and messages only) from GitLab CI to GitHub workflows; its lint, `ci_hardening_lint.py`, is rewritten and is not frozen. After that all eight scripts are frozen for good.
- `ci_hardening_lint.py` now lints `.github/workflows` and `.github/actions` (pinned `uses:`, permissions, `pull_request_target`, inline secrets, image digests, osv coverage, environment-scoped secrets, timeouts, `DRY_RUN`, documented permissions). CI runs it with actionlint and zizmor as the blocking `workflows` job.
- `devsec.sh` derives `GITHUB_SHA`, `GITHUB_REF_NAME`, `GITHUB_REPOSITORY` and `GITHUB_SERVER_URL` from git; local images are tagged `ghcr.io/<owner>/<repo>/<id>:local-<short sha>` (lowercased). The `.env.local` override `CI_PROJECT_PATH` is now `GITHUB_REPOSITORY`.
- Merged SARIF reports are named `<tool>.sarif` instead of `gl-<tool>.sarif.json`.

### Removed
- `infra/security/publish-sbom-registry.sh`, the `devsec.sh publish` subcommand and its tests; the SBOM is attested and published as a release asset instead.

---

## [3.0.0] — 2026-11-12

### Added
- Full PostgreSQL CRUD sample with Items feature (backend + frontend)
- Next.js 15 frontend with React 18, TypeScript 5.9, Tailwind CSS 3
- React Query v5 for server state management
- React Hook Form + Zod for form validation
- Playwright E2E tests for frontend
- Vitest unit and integration tests with MSW mocking
- Typed API client generated from OpenAPI spec
- Dark/light theme with CSS variable-based design tokens
- Toast notification system
- ARIA live region for accessibility feedback
- ConfirmModal component for destructive actions
- Item search, sorting, and pagination in the list view
- Full CRUD operations (Create, Read, Update, Delete) for items
- Frontend test suite: unit, integration (RTL+MSW), E2E (Playwright)
- **Kubernetes deployment via a Helm chart** (per-env `values.<env>.yaml` for dev/qa/production/local)
- **NGINX Ingress Controller** as the edge gateway
- **NetworkPolicy** for network isolation
- **HorizontalPodAutoscaler** for automatic scaling
- **PodDisruptionBudget** for availability guarantees
- **ServiceMonitor** for Prometheus metrics scraping
- **Operations Runbook** for Kubernetes operational procedures
- **Database migration Job** for automated schema updates
- **PostgreSQL backup CronJob** for automated backups
- **Request correlation ID** (`X-Request-Id`) injected on every request and carried onto every log
  record, with structured JSON logs in production (see the
  [operations runbook](./operations-runbook.md#log-correlation--tracing))
- AuthN/authZ for the sample API: gateway-trusted-header and HMAC-signed JWT (HS256/384/512) via
  `get_current_user` / `require_roles`, enforced on the sample write endpoints
- Defense-in-depth response headers: `Permissions-Policy` and `Cross-Origin-Resource-Policy`
- OpenAPI contract single-sourced via `python -m your_service_name.backend.openapi dump` (+ Make targets
  and CI drift gates)
- `values.schema.json` (draft-07) validates Helm values at `helm lint` / `helm template` time
- `useHealth` React Query hook drives the home page
- Apache-2.0 `LICENSE.txt`

### Changed
- Frontend migrated from Streamlit to Next.js 15 + React + TypeScript
- Upgraded to FastAPI with SQLAlchemy 2.0 async patterns
- Replaced sync database with async PostgreSQL (asyncpg) + SQLite fallback
- Moved to src-layout for Python package
- Updated to GitLab CI with 11-stage pipeline (added deploy stage)
- **Migrated from Docker Compose to Kubernetes** for production deployment
- **Adopted the NGINX Ingress Controller** as the edge gateway
- **Migrated from Kustomize overlays to a Helm chart** for multi-environment deployment (dev/qa/production/local)
- **Enhanced security** with NetworkPolicy, PodSecurity, RBAC, sealed-secrets support
- NGINX Ingress Controller handles all edge routing in production
- Split monolithic Dockerfile into per-service Dockerfiles (`Dockerfile.backend`, `Dockerfile.frontend`, `Dockerfile.nginx`)
- Removed `SERVICE_MODE` switching; each container runs one service directly
- Updated `docker-compose.yml` to 4 services (postgres, backend, frontend, nginx)
- Updated K8s manifests to use direct service commands (gunicorn, node, nginx)
- Updated CI pipeline to build and deploy per-service images

### Removed
- `/items/{id}/archive` endpoint and `archive_item` (canonical soft-delete via `delete_item`)
- Dead `sort` / `order` query params from the Item sample
- ReDoc (`/redoc`) and the self-hosted Swagger experiment (`docs_ui.py`, `swagger-ui-dist`,
  `scripts/vendor-redoc.sh`, and the vendored `redoc.standalone.js`)
- Broken `podman-compose.yml`
- Dead `BACKEND_ENTRY=.env` pattern; `PytestUnhandledThreadExceptionWarning` ignore

### Security
- Content-Security-Policy headers on frontend
- Rate limiting (per-IP, 100 req/min, enforced in middleware)
- Non-root container user (UID 10001)
- SBOM generation with Syft
- Vulnerability scanning with Grype
- License compliance scanning with Grant
- **NetworkPolicy** default deny + explicit allow rules
- **Pod Security Standards** (restricted profile)
- **RBAC** minimal permissions via ServiceAccount/Role/RoleBinding
- **Resource Quotas** per namespace
- **AuthN/authZ** on the sample write endpoints (gateway-trusted-header + HMAC-signed JWT)
- **CORS** restrictive same-origin default (empty when `CORS_ORIGINS` unset)
- **CSP** without `unsafe-inline` / `unsafe-eval` (per-request nonce)

### Fixed
- Rate limiting actually enforces per-IP (replaced the slowapi no-op on FastAPI 0.162)
- UUID is a real `Uuid` column (runtime `uuid.UUID`); `update_item` restricted to a writable-field allowlist
- OpenAPI drift gate made stable (`API_CONTRACT_VERSION`) and git-free on the frontend image
- Empty GitLab SAST SARIF emitted on skipped OSV/GuardDog scans so the MR Security widget still gets a report
- distroless frontend probes and healthcheck use `/nodejs/bin/node`; port-forward scheme resolved by numeric index
- Docs accuracy: changelog Kustomize→Helm, getting-started fence, deploy example,
  `environment-variables.md` reconciled to `.env.example` (no `DEBUG=TRUE`), supply-chain doc, `doc-templates`
- Troubleshooting: stale-frontend-image `POST /items` "Service temporarily unavailable" (500) root cause + fix

---

## [2.2.0] — Streamlit Frontend Era

### Added
- Streamlit-based frontend
- Nginx for static asset serving
- Docker Compose multi-service orchestration
- BuildKit rootless container builds
- GitLab CI/CD with 10-stage pipeline
- SBOM and security scanning in CI

---

## [2.1.0]

### Added
- Echo service sample (in-memory)
- Health endpoints (`/health`, `/healthz`)
- API versioning system
- Alembic database migrations
- SQLite fallback when PostgreSQL unavailable

---

## [2.0.0]

### Added
- FastAPI backend with layered architecture
- SQLAlchemy 2.0 ORM
- Pydantic v2 schemas
- Gunicorn + Uvicorn ASGI server
- Docker multi-stage build
- Apptainer/Singularity support
- Git tag-based versioning (setuptools_scm)

---

## [1.0.0]

### Added
- Initial project template
- Basic Python package structure
- Simple REST API
- Docker support
- Basic CI/CD pipeline
