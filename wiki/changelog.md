# Changelog

All notable changes to this project, following [Keep a Changelog](https://keepachangelog.com/) format.

---

## [Unreleased]

<!-- Future changes accumulate here. When cutting a release, rename this heading to the
     version + date and open a fresh [Unreleased] above it (see wiki/releasing.md). -->

### Added
- **Typed settings.** One `pydantic-settings` class, `Settings` (`backend/settings.py`), declares every environment variable the backend reads (including `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `TEST_DATABASE_URL`, `SQLITE_PATH` and the `AUTH_*` family), with a type, the existing default and a description; `create_app()` builds it once and keeps it on `app.state.settings`, and a test fails if anything else in `src/` touches `os.environ`. Names and defaults are unchanged. An invalid configuration now stops startup with one error that names every invalid variable (and never prints a secret). `wiki/environment-variables.md` has a generated backend-settings section (`make env-docs`; `make env-docs-check` runs in `check-all-dry` and CI).
- **Production rules.** With `ENV=production` (or `prod`) the backend refuses to start on SQLite or no database, credentialed CORS with an empty or `*` origin list, a CORS origin that is not a bare `scheme://host[:port]`, `AUTH_MODE=none` without `ALLOW_ANONYMOUS=true`, any `TEST_DATABASE_URL`, and the Swagger UI or the OpenAPI JSON turned on without `ALLOW_PRODUCTION_DOCS=true`. `/docs` and `/openapi.json` are now off by default in production; new variables `OPENAPI_URL`, `ALLOW_ANONYMOUS` and `ALLOW_PRODUCTION_DOCS`. The chart's production values do not choose anonymous access: set `AUTH_MODE` (for example `gateway`) under `config:` before deploying, or the migration hook fails and the release rolls back.
- **Weekly re-scan of the running and released images.** `rescan.yml` (Wednesday 05:41 UTC and manual) takes the newest successful deployment of each environment and the latest release, finds their digests through the run named in the deployment status (release assets, else the `image-digests` artifact of that run attempt; an expired artifact or a rollback is reported as not scannable), runs Grype by digest with the repository's ignore list and no VEX, and opens or updates one `vuln` issue per (vulnerability, package) for critical and high findings. Three jobs: `collect`, `scan` (the only one with `packages: read`) and `report` (`issues: write`). The release's draft now also carries `image-digests.env` and `scan.env`. New `wiki/vulnerability-management.md` sets the SLA (critical 7 days, high 30) and how a risk is accepted. `deploy-ops.yml` now records a deployment only for a rollback (`deployment: false` for the other operations).
- **Renovate, pin refresh and a blocking pin-drift check.** `renovate.json5` (inert until the app is installed) covers `tools/pins.tsv`, the annotated versions in `infra/security/config.yaml`, image digests, Dockerfiles and `uses:` pins, with a 7-day release age, one weekly group for minor and patch updates, and separate majors. `make pins-refresh` (`scripts/pins-refresh.sh`) writes the vendor-published checksum for every pin that differs and regenerates the hashed locks; `--check` is read-only. The new blocking `pins` job (behind `CI OK`, skippable on a pull request that touches no pin file) fails when a recorded checksum differs from the vendor's for that version, on changed rows for a pull request and on every row for `main` and the weekly `pins-drift.yml`. `.github/dependabot.yml` keeps Dependabot to security updates (`open-pull-requests-limit: 0`). The check anchors each pin to its vendor's url prefix, verifies every lock entry against PyPI (hashes and a 7-day age), retries transient fetch failures, and checks every row when the checker itself changes. `github-settings.sh` also turns on Dependabot alerts and security updates.
- **OpenSSF Scorecard.** `scorecard.yml` runs weekly and on push to `main`, publishes the result and uploads SARIF to code scanning (a private repository opts out with the variable `SCORECARD_ENABLED=false`; assurance, not a gate). The README shows the Scorecard badge. The hardening lint exempts the `analysis` job of `scorecard.yml` (and only it) from the environment rule for `id-token: write`, which publishing needs.
- **Pre-commit hooks and a blocking `hadolint` job.** `.pre-commit-config.yaml` has local hooks (ruff format, ruff, prettier, eslint, shellcheck, hadolint, gitleaks on staged changes, actionlint, yamllint for the chart values, and a placeholder-leftover check) that call the tool CI uses; `make precommit` runs them with the pinned pre-commit. The `hadolint` job lints every tracked Dockerfile and is behind `CI OK`. Dockerfile fixes: numeric `USER` (the nginx user is now UID 10001, matching the chart), exec-form `HEALTHCHECK`, merged `RUN` layers, pinned pip.
- **Isolated build, keyless signing and attestations.** `reusable-build.yml` is now the only workflow that pushes, signs or attests images (`build`, `sbom`, `grype`, then `sign-attest`); `release.yml` calls it. After the blocking Grype gate each image digest is signed keyless with the pinned cosign, verified against the workflow identity, and given a SLSA provenance and a CycloneDX SBOM attestation (`push-to-registry`). On `v*` tags the SBOMs, the OpenVEX document and the licence report are uploaded to a draft GitHub Release (never published by the workflow). See `wiki/security.md` for how to verify an image.
- The signing step also verifies the workflow repository claim (a foreign repository can call a public reusable workflow and get the same identity), and the attestations run only for public repositories or when the variable `ATTESTATIONS_ENABLED` is `true`. Every `reusable-build.yml` job refuses to run outside a push to `main` or a `v*` tag.
- `ci_hardening_lint.py`: new check `image-push-isolation` (it also lints composite actions, `write-all`, buildx/bake/crane/oras pushes and pull_request callers), and `provenance` is now a real check (it no longer prints a SKIP). The JSON report has no `skipped` field any more.

### Changed
- A root `.dockerignore` keeps host-only files out of every image build: `node_modules` (host-built native binaries overwrote the clean `npm ci` in local and kind builds), every `.env*` file (Next.js reads them at build time), `.git`, `.venv`, `.tools`, build and test output. A test checks it against every `COPY` source of the three Dockerfiles.
- An unknown `AUTH_MODE`, `AUTH_MODE=bearer` without `AUTH_JWT_SECRET`, a non-HMAC `AUTH_JWT_ALGORITHM`, an unsupported `API_VERSION`, an invalid `RATE_LIMIT_DEFAULT` or a flag that is not a boolean now fail at startup instead of at the first request. `get_api_version()` is removed (read `Settings().API_VERSION`), `build_api_router(version=...)` takes a string, and `run_migrations()` takes a `Settings`.
- ruff now covers `src/ tests/ scripts/ infra/ .github/scripts/` and no longer auto-fixes (`fix = true` is gone); only `make check-all` fixes, through `make ruff-lint-fix`. The chart values files are reformatted (output byte-identical), so `ct lint` passes. The `workflows` job installs PyYAML from `tools/requirements/pyyaml.txt` instead of the backend extras.
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
