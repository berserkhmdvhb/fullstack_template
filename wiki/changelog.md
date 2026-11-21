# Changelog

All notable changes to this project, following [Keep a Changelog](https://keepachangelog.com/) format.

---

## [Unreleased]

<!-- Future changes accumulate here. When cutting a release, rename this heading to the
     version + date and open a fresh [Unreleased] above it (see wiki/releasing.md). -->

### Fixed
- **The release OpenVEX no longer claims `not_affected` for fixable findings.** `generate-vex.sh` marked every Grype match with an upstream fix `not_affected` / `fixed_version`, although the shipped image still contained the vulnerable version. Every Grype finding (`matches` and `ignoredMatches`) is now `affected` with an `action_statement`; `not_affected` comes only from the reviewed list `infra/security/local/vex-not-affected.json` (justification, impact statement, review and expiry dates; expired entries are ignored with a warning, malformed ones fail the job). A one-time, owner-approved edit to a frozen script. No release had been published. See [Vulnerability management](./vulnerability-management.md#the-releases-openvex).

### Added
- **Opt-in tracing, Redis rate-limit storage and CSP reports (all off by default).** `TRACING_ENABLED` exports OpenTelemetry traces over OTLP/HTTP (`OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS`, `OTEL_SERVICE_NAME`, `TRACING_SAMPLE_RATIO`): server and database spans (statement text only), the probes and `/metrics` excluded, no header, body, query string or SQL parameter captured, a batch exporter that never slows a request; the log `trace_id`/`span_id` and the problem body's `trace_id` come from the active span. The rate limiter now uses the async `limits` API and `RATE_LIMIT_STORAGE` (a secret; `memory://`, `redis://`, `rediss://`, `redis+unix://`; production refuses plaintext `redis://` to a remote host) with `RATE_LIMIT_STORAGE_TIMEOUT_SECONDS` (0.2): it fails open on a storage error. `CSP_REPORT_ENABLED` serves `POST /api/v1/csp-report` (size-capped, rate limited, sanitised log lines; not in the OpenAPI document), and the frontend adds the reporting directives when `CSP_REPORTING_ENABLED` is set. The chart gets `observability.tracing.*`, `cspReporting.enabled` and `backend.networkPolicy.extraEgress`. New dependencies: `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`, `opentelemetry-instrumentation-fastapi`, `opentelemetry-instrumentation-sqlalchemy`, `redis` (imported only when the feature is on). See [Backend architecture](./backend-architecture.md#tracing) and [Security](./security.md#rate-limiting).
- **Real-PostgreSQL tests and API contract gates.** Integration, e2e and contract tests run on PostgreSQL 16 in CI (a `postgres` service container pinned by digest, the same major and variant as the chart's database), and on SQLite locally unless `TEST_DATABASE_URL` is a PostgreSQL URL; the schema is made by the migrations and each test empties the tables afterwards. A migration round-trip test (upgrade, downgrade to base, upgrade) runs on both. The blocking `lint` job runs squawk on the migration SQL rendered offline for PostgreSQL (`scripts/ci/squawk.sh`, `.squawk.toml`). Schemathesis contract tests (`make test-contract`, marker `contract`, outside `make test`) run in the integration job with a fixed seed. The blocking `api-compat` job (`scripts/ci/oasdiff.sh`) fails a pull request that breaks the OpenAPI snapshot, unless it carries the `breaking-change` label; `labeled` and `unlabeled` are now pull request event types and every job runs on them. `api/deprecation.py` sets `Deprecation` (RFC 9745) and `Sunset` (RFC 8594) headers. See [API lifecycle](./api-lifecycle.md) and [Testing](./testing.md#postgresql-contract-tests-and-the-two-gates). The contract tests found three defects, fixed without changing the published contract: `GET /items?page=` beyond a 64-bit offset and a NUL character in item text were 500s on PostgreSQL (now an empty page and a 422), and `/metrics` documented JSON for a text response. New dev dependency: `schemathesis`.
- **Database hardening.** `database.tls.caSecret` mounts a database CA from an existing Secret (read-only, backend and migration Job) and sets `DB_SSLROOTCERT`; `backend.extraInitContainers` renders init containers, such as a Cloud SQL Auth Proxy native sidecar, before `wait-for-db`. A `DATABASE_URL` carrying `ssl` or `sslmode` is refused, and in production a URL naming no host no longer counts as loopback. The migration Job keeps its logs after an automatic rollback (`helm.sh/resource-policy: keep`), and the runner retries only connection failures, never authentication or TLS errors. `DB_CONNECT_TIMEOUT_SECONDS` (10), `DB_STATEMENT_TIMEOUT_MS` (30000) and `DB_IDLE_IN_TRANSACTION_TIMEOUT_MS` (60000) apply to the app's PostgreSQL connections; `DB_MIGRATION_LOCK_TIMEOUT_MS` (10000) gives the migration run a `lock_timeout` and no statement timeout. `DB_SSLMODE` (`disable`, `require`, `verify-ca`, `verify-full`) and `DB_SSLROOTCERT` set TLS to the database; production refuses `disable` and `require` unless the database host is loopback (a Cloud SQL Auth Proxy sidecar), and the production chart values set `verify-full`. Timestamps are timezone-aware UTC on every database (no schema change). See [Database operations](./database.md#database-operations).
- **Schema-aware readiness.** `GET /api/v1/health` answers 200 when the database is reachable and at this release's head or at a revision this release does not know (a newer release migrated first), and 503 with `reason` `database_unreachable`, `schema_missing` or `schema_behind` otherwise. `/api/v1/healthz` stays process-only.
- **Production authentication.** `AUTH_MODE=oidc` verifies an `Authorization: Bearer` JWT (RS256/ES256 by default; an allowlist of RS*, PS*, ES*) against the provider's JWKS, and `AUTH_MODE=cloudflare-access` verifies the `Cf-Access-Jwt-Assertion` header against the team's `/cdn-cgi/access/certs` keys and the application's AUD tag (the cookie is ignored). Checked: algorithm allowlist and key type (so `none` and HS/RS confusion fail), required `kid`, no `crit`, exact issuer, audience, required `exp`, `nbf`/`iat` with `OIDC_CLOCK_SKEW_SECONDS`, tokens up to 8 KiB. The JWKS is cached (`JWKS_CACHE_TTL_SECONDS`), fetched once for concurrent cold requests, refetched for an unknown `kid` at most once per `JWKS_MIN_REFETCH_SECONDS`, and served stale for up to `JWKS_MAX_STALE_SECONDS` when the provider is down, then `503`. Failures are `401` problems with `WWW-Authenticate` (`error="invalid_token"`) and a generic detail. New variables `OIDC_*`, `CF_ACCESS_*` and `JWKS_*`; new dependency `pyjwt[crypto]` (adds `cryptography`). Production accepts both modes without `ALLOW_ANONYMOUS`; `gateway` and `bearer` stay as reference modes. See `wiki/security.md#authentication--authorization`.
- **Request safety.** `X-Request-Id` is kept only when it is at most 64 characters of `[A-Za-z0-9._-]` (otherwise a new id is generated, and the rejected value is never logged) and is echoed on every response. A valid W3C `traceparent` puts its trace id on every log line (`trace_id`, JSON and text) and on error bodies; `outgoing_trace_headers()` forwards it. `MAX_REQUEST_BODY_BYTES` (default 10 MiB, equal to the chart's ingress `proxy-body-size`) answers `413`, and `ALLOWED_HOSTS` (default `*`; production refuses `*` or an empty list) answers `400` for any other `Host`, with the health and metrics paths exempt. The chart renders `ALLOWED_HOSTS` from the ingress hosts, the backend Service names and `backend.extraAllowedHosts` (`values.local.yaml` adds `localhost,127.0.0.1`); `smoke-test.sh` has a `--host` option, which the deploy and e2e scripts use with the backend Service name. The rate limiter and the host check skip exact probe and metrics paths. See `wiki/security.md#request-limits-and-trusted-hosts`.
- **Trusted client IP.** `TRUSTED_PROXY_CIDRS` (default empty) and `TRUST_CF_CONNECTING_IP` (default `false`) drive one resolver, `ClientIpMiddleware`, the outermost middleware: `CF-Connecting-IP` is believed only from a peer in those ranges and only when its flag is on, `X-Forwarded-For` only from such a peer (read from the right, skipping trusted hops), and every other peer is the client. The rate limiter and the request log (now `... client=<ip>`) use it, so clients behind the gateway no longer share one bucket (previously every client behind a proxy outside Uvicorn's default `127.0.0.1` allow-list keyed on the proxy's address). Production refuses `TRUST_CF_CONNECTING_IP=true` with no ranges. `TRUSTED_PROXY_CIDRS` refuses entries with host bits set (`10.1.2.3/8`). See `wiki/security.md#client-ip` for what the proxies must do per edge mode. The chart sets both in `config:`, documents that with the WAF on the backend sees `X-Forwarded-Proto: http`, and sets Envoy's upstream idle timeout (`waf.upstreamIdleTimeoutSeconds`, 60 s; Envoy's default is one hour) below the backend keep-alive.
- **Typed settings.** One `pydantic-settings` class, `Settings` (`backend/settings.py`), declares every environment variable the backend reads (including `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `TEST_DATABASE_URL`, `SQLITE_PATH` and the `AUTH_*` family), with a type, the existing default and a description; `create_app()` builds it once and keeps it on `app.state.settings`, and a test fails if anything else in `src/` touches `os.environ`. Names and defaults are unchanged. An invalid configuration now stops startup with one error that names every invalid variable (and never prints a secret). `wiki/environment-variables.md` has a generated backend-settings section (`make env-docs`; `make env-docs-check` runs in `check-all-dry` and CI).
- **Production rules.** With `ENV=production` (or `prod`) the backend refuses to start on SQLite or no database, credentialed CORS with an empty or `*` origin list, a CORS origin that is not a bare `scheme://host[:port]`, `AUTH_MODE=none` without `ALLOW_ANONYMOUS=true`, any `TEST_DATABASE_URL`, and the Swagger UI or the OpenAPI JSON turned on without `ALLOW_PRODUCTION_DOCS=true`. `/docs` and `/openapi.json` are now off by default in production; new variables `OPENAPI_URL`, `ALLOW_ANONYMOUS` and `ALLOW_PRODUCTION_DOCS`. The chart's production values do not choose anonymous access: set `AUTH_MODE` (for example `gateway`) under `config:` before deploying, or the backend Pods never become ready and the release rolls back.
- **Weekly re-scan of the running and released images.** `rescan.yml` (Wednesday 05:41 UTC and manual) takes the newest successful deployment of each environment and the latest release, finds their digests through the run named in the deployment status (release assets, else the `image-digests` artifact of that run attempt; an expired artifact or a rollback is reported as not scannable), runs Grype by digest with the repository's ignore list and no VEX, and opens or updates one `vuln` issue per (vulnerability, package) for critical and high findings. Three jobs: `collect`, `scan` (the only one with `packages: read`) and `report` (`issues: write`). The release's draft now also carries `image-digests.env` and `scan.env`. New `wiki/vulnerability-management.md` sets the SLA (critical 7 days, high 30) and how a risk is accepted. `deploy-ops.yml` now records a deployment only for a rollback (`deployment: false` for the other operations).
- **Renovate, pin refresh and a blocking pin-drift check.** `renovate.json5` (inert until the app is installed) covers `tools/pins.tsv`, the annotated versions in `infra/security/config.yaml`, image digests, Dockerfiles and `uses:` pins, with a 7-day release age, one weekly group for minor and patch updates, and separate majors. `make pins-refresh` (`scripts/pins-refresh.sh`) writes the vendor-published checksum for every pin that differs and regenerates the hashed locks; `--check` is read-only. The new blocking `pins` job (behind `CI OK`, skippable on a pull request that touches no pin file) fails when a recorded checksum differs from the vendor's for that version, on changed rows for a pull request and on every row for `main` and the weekly `pins-drift.yml`. `.github/dependabot.yml` keeps Dependabot to security updates (`open-pull-requests-limit: 0`). The check anchors each pin to its vendor's url prefix, verifies every lock entry against PyPI (hashes and a 7-day age), retries transient fetch failures, and checks every row when the checker itself changes. `github-settings.sh` also turns on Dependabot alerts and security updates.
- **OpenSSF Scorecard.** `scorecard.yml` runs weekly and on push to `main`, publishes the result and uploads SARIF to code scanning (a private repository opts out with the variable `SCORECARD_ENABLED=false`; assurance, not a gate). The README shows the Scorecard badge. The hardening lint exempts the `analysis` job of `scorecard.yml` (and only it) from the environment rule for `id-token: write`, which publishing needs.
- **Pre-commit hooks and a blocking `hadolint` job.** `.pre-commit-config.yaml` has local hooks (ruff format, ruff, prettier, eslint, shellcheck, hadolint, gitleaks on staged changes, actionlint, yamllint for the chart values, and a placeholder-leftover check) that call the tool CI uses; `make precommit` runs them with the pinned pre-commit. The `hadolint` job lints every tracked Dockerfile and is behind `CI OK`. Dockerfile fixes: numeric `USER` (the nginx user is now UID 10001, matching the chart), exec-form `HEALTHCHECK`, merged `RUN` layers, pinned pip.
- **Isolated build, keyless signing and attestations.** `reusable-build.yml` is now the only workflow that pushes, signs or attests images (`build`, `sbom`, `grype`, then `sign-attest`); `release.yml` calls it. After the blocking Grype gate each image digest is signed keyless with the pinned cosign, verified against the workflow identity, and given a SLSA provenance and a CycloneDX SBOM attestation (`push-to-registry`). On `v*` tags the SBOMs, the OpenVEX document and the licence report are uploaded to a draft GitHub Release (never published by the workflow). See `wiki/security.md` for how to verify an image.
- The signing step also verifies the workflow repository claim (a foreign repository can call a public reusable workflow and get the same identity), and the attestations run only for public repositories or when the variable `ATTESTATIONS_ENABLED` is `true`. Every `reusable-build.yml` job refuses to run outside a push to `main` or a `v*` tag.
- `ci_hardening_lint.py`: new check `image-push-isolation` (it also lints composite actions, `write-all`, buildx/bake/crane/oras pushes and pull_request callers), and `provenance` is now a real check (it no longer prints a SKIP). The JSON report has no `skipped` field any more.

### Changed
- **Frontend on Next.js 16 and React 19.** `next` 16.4.6 (Turbopack builds; the standalone output is unchanged), `react` and `react-dom` 19.3.1, and the matching types and ESLint plugin; `frontend/middleware.ts` is now `frontend/proxy.ts` (the `proxy` export): the same backend proxy, CSP nonce, headers and matcher. The runtime libraries moved to their newest release in the same major.
- **Node moves to the 26 Active LTS.** The pinned Node (`tools/pins.tsv`), `frontend/.nvmrc`, the new `frontend/.node-version` and the Dockerfile base images follow one version (26.13.1); `engines.node` is `>=26 <27`, `packageManager` names the npm bundled with that Node (`frontend/scripts/check-npm-version.mjs` fails the frontend CI jobs and the image builds on any other npm), Renovate caps Node at 26.x in every file that names it, and the builder images are Debian 13 (trixie), the release the distroless runtime uses. Tests lock each to the pin. `@types/node` follows the runtime major.
- **Frontend dev tooling.** TypeScript 6.0 (`baseUrl` removed from `tsconfig.json`, `paths` resolve from the config; 7 is held until `typescript-eslint` accepts it), ESLint stays on 9 (10 is held until the React, import and jsx-a11y plugins accept it), `eslint-plugin-vitest` replaced by `@vitest/eslint-plugin` with the same rules, the other ESLint plugins updated, and a scoped override lets `openapi-typescript` use TypeScript 6 (the generated types are byte-identical). Vitest 5, jsdom 31, MSW 3, `@testing-library/jest-dom` 8, `@hookform/resolvers` 7, Playwright 1.65 and Prettier 3.10; the tests and coverage thresholds are unchanged.
- **Pre-release correction: `/metrics` is no longer in the OpenAPI document.** It is the operational Prometheus scrape endpoint, not part of the API contract; it is still served. The snapshot and the generated types dropped it. The `api-compat` gate will flag this on the pull request that carries it; the `breaking-change` label covers it.
- **Migrations run before the app, not inside it** (breaking for anyone who relied on the app creating its own schema). The lifespan no longer migrates. The chart's migration Job is now a plain release resource named `<fullname>-migrate-r<revision>` (24 hour TTL), no longer a Helm hook: Helm waits for the release's resources before it runs post-install hooks, so a hook would deadlock the first install now that readiness needs the schema. `deploy-env.sh` passes `--wait-for-jobs`, so a failed migration fails the release and the rollback runs. The runner leaves a database that is ahead of the release alone, so `helm rollback` is safe, and migrations follow an expand/contract policy. Local entry points (`run_backend.sh`, `infra/runtime/entrypoint.sh`, a compose `migrate` service, the CI job that boots Uvicorn) migrate first. See [Database operations](./database.md#the-migration-job). **Rollback limit:** a release from before this change runs no migration Job, and its own startup migration fails on a revision it does not know, so once a later release has added a migration you cannot roll back past this release.
- **API error bodies are now RFC 9457 `application/problem+json`** (breaking for API clients that read the old body). Every error, including the rate limiter's `429`, an unknown path's `404` and an unexpected `500`, has `type`, `title`, `status`, `detail`, `instance` (the path, no query string) and `request_id`. A `422` no longer has `detail` as an array: the fields are in `errors` (`loc`, `msg`, `type`, never the submitted value) and `detail` is a string. A `500` carries no exception text. The OpenAPI document replaces `HTTPValidationError` with `ProblemDetails` and `ValidationProblem`, and the frontend error mapper reads them. See `wiki/api-reference.md#error-responses`.
- **Uvicorn only; Gunicorn is gone.** The image already ran one Uvicorn process; every remaining Gunicorn mention in code, docs and `.env.example` is removed, and a test keeps it out. The launcher (`infra/backend/run_backend.sh`) now passes `--no-proxy-headers`, `--no-server-header`, `--no-access-log` (the app writes its own request log) and `--timeout-keep-alive ${BACKEND_KEEPALIVE_TIMEOUT}` (default 75 s). The root `run_backend.sh` is the developer launcher (`uvicorn --reload`). Replacements for the removed variables: `GUNICORN_WORKERS` none (one process per pod; scale pods); `GUNICORN_RELOAD=true` run `./run_backend.sh`; `GUNICORN_TIMEOUT` none (Uvicorn has no worker timeout; `BACKEND_GRACEFUL_TIMEOUT` bounds the shutdown drain and `BACKEND_KEEPALIVE_TIMEOUT` the idle connection).
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
