# your-service-name — adaptable service template (see wiki/getting-started.md)
.PHONY: all help install develop lock lock-check \
    export-requirements check-requirements-sync \
    ruff-format ruff-format-check ruff-lint ruff-lint-fix mypy-check \
    openapi-gen openapi-drift-check env-docs env-docs-check \
    shellcheck docs-links image-checks frontend-deployment-id tools tools-dry-run pins-refresh \
    check-all check-all-dry precommit chart-unittest kubeconform rules-test crd-schemas crd-schemas-check \
    test test-unit test-integration test-e2e test-contract test-live test-file test-file-function test-fast \
    test-coverage test-coverage-xml test-cov-html test-coverage-rep test-coverage-file \
    check-updates check-toml \
    clean clean-build clean-logs clean-cache clean-coverage clean-pyc clean-all \
    build \
    compose-up compose-down compose-down-v compose-ps compose-logs compose-restart \
    compose-build compose-pull \
    dev-tools-list dev-tools-outdated dev-tools-upgrade version-check- \
    k8s-apply k8s-delete k8s-build k8s-status k8s-pods k8s-services k8s-port-forward \
    k8s-wait k8s-logs k8s-port-forward-all deploy rebuild-and-roll smoke-test \
    kind-up kind-deploy kind-down kind-shutdown-test kind-migration-test kind-deploy-env-test kind-preview-test kind-admission-test \
    kind-tracing-redis-test kind-csp-report-test kind-live kind-smoke kind-e2e kind-zap \
    secrets db-backup certs \
    security-local security-build security-tools \
    security-sbom security-vuln security-license security-malware security-iac security-k8s security-dast security-vex security-audit \
    security-sbom-quality security-sbom-validate \
    security-report security-report-sbom security-report-vuln security-report-osv security-report-vex security-report-license security-report-origin security-scan-search \
    security-publish \
    security-clean

# -------------------------------------------------------------------
# Config
# -------------------------------------------------------------------
# Prefer the project virtualenv so package-backed targets (openapi-*, tests, ...) work
# without manually `source .venv/bin/activate`; fall back to a system Python otherwise.
PYTHON := $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else command -v python3 || command -v python; fi)
# uv: the pinned binary from tools/pins.tsv (scripts/tools.sh install uv) when present, else the one on PATH.
UV := $(or $(wildcard .tools/bin/uv),uv)
SERVICE := your_service_name
# The project venv's tools (ruff, mypy, uvicorn) first, so make finds them without activating it.
export PATH := $(CURDIR)/.venv/bin:$(PATH)
# COV_FAIL_UNDER intentionally absent: the 85% gate is single-sourced in
# pyproject [tool.coverage.report] fail_under, so any `--cov` run enforces it.

# Auto-extract dev tools from pyproject.toml [dependency-groups] dev (names only; strip pins/extras)
ESC := $(shell printf '\033')
DEV_TOOLS := $(shell \
    $(PYTHON) -c "import re,tomllib,pathlib; d=tomllib.loads(pathlib.Path('pyproject.toml').read_text()); dev=[x for x in d.get('dependency-groups',{}).get('dev',[]) if isinstance(x,str)]; names=[]; [names.append(re.split(r'[<>=!~\\[]', s, maxsplit=1)[0].strip()) for s in dev]; print(' '.join(sorted(set(filter(None,names)))))" \
)
# -------------------------------------------------------------------
# Default
# -------------------------------------------------------------------
all: help

# -------------------------------------------------------------------
# Help
# -------------------------------------------------------------------
help::
	@echo "Available Makefile commands:"
	@echo ""
	@echo "  install                  Install the locked runtime dependencies (uv sync --locked --no-dev)"
	@echo "  develop                  Install the locked set with the dev group (uv sync --locked)"
	@echo "  lock                     Re-resolve uv.lock (releases at least 7 days old; see wiki/getting-started.md)"
	@echo "  lock-check               Fail if uv.lock is stale against pyproject.toml"
	@echo "  export-requirements      Export the hashed runtime requirements from uv.lock (REQUIREMENTS=requirements.txt)"
	@echo "  check-requirements-sync  Fail if uv.lock is stale against pyproject.toml (same as lock-check)"
	@echo ""
	@echo "  ruff-format              Auto-format code using Ruff"
	@echo "  ruff-format-check        Check code formatting (dry run)"
	@echo "  ruff-lint                Run Ruff linter"
	@echo "  mypy-check               Run MyPy static type checker"
	@echo "  shellcheck               Lint all tracked shell scripts (*.sh + *.bats) with ShellCheck"
	@echo "  docs-links               Check every file link and #anchor in the docs, offline (pinned lychee, lychee.toml)"
	@echo "  image-checks             Build the images (throwaway tags) and run the CI structure checks (IDS=...)"
	@echo "  frontend-deployment-id   Build the frontend image and check the page carries the deployment id"
	@echo "  check-all                Run dev tool drift check + format + lint + mypy + env-docs (rewrites files)"
	@echo "  check-all-dry            Same as above but read-only (format-check, env-docs-check)"
	@echo "  precommit                Run every pre-commit hook on all files (pinned pre-commit, no network)"
	@echo "  chart-unittest           Run the chart's helm-unittest suites (pinned plugin, no cluster)"
	@echo "  kubeconform              Validate every chart and bootstrap render, strict and offline (local schemas only)"
	@echo "  rules-test               Check the chart's alert and recording rules (both flavours) with promtool, and run its rule unit tests"
	@echo "  crd-schemas              Regenerate the vendored CRD schemas from their pinned sources (fetches, sha256-checked)"
	@echo "  crd-schemas-check        Fail if the vendored CRD schemas differ from what their pinned sources generate"
	@echo "  openapi-gen              Regenerate openapi.json + TS types from the backend single source"
	@echo "  openapi-drift-check      Fail if committed openapi.json / types.gen.d.ts is stale (CI mirror)"
	@echo "  env-docs                 Regenerate the backend-settings block of wiki/environment-variables.md"
	@echo "  env-docs-check           Fail if that block is stale (also part of check-all-dry and CI)"
	@echo ""
	@echo "  dev-tools-list           Show dev tools detected from pyproject.toml"
	@echo "  dev-tools-outdated       List dev tools with a newer release (uv tree --outdated; read-only)"
	@echo "  dev-tools-upgrade        Re-lock the dev tools to their newest allowed release, then sync"
	@echo "  version-check-<pkg>      Check if an upgrade is available for a single package"
	@echo ""
	@echo "  test                     Run all hermetic test layers (unit + integration + e2e) using Pytest"
	@echo "  test-unit                Run only unit tests using Pytest"
	@echo "  test-integration         Run integration tests using Pytest"
	@echo "  test-e2e                 Run end-to-end tests using Pytest (-m e2e)"
	@echo "  test-contract            Run the Schemathesis contract tests (-m contract)"
	@echo "  test-live                Run live backend smoke tests (-m live)"
	@echo "  test-file                Run a single test file or keyword with FILE=..."
	@echo "  test-file-function       Run a specific test function with FILE=... FUNC=..."
	@echo "  test-fast                Run only last failed tests"
	@echo ""
	@echo "  test-coverage            Run tests and show terminal coverage summary"
	@echo "  test-coverage-xml        Run tests and generate XML coverage report"
	@echo "  test-cov-html            Run tests with HTML coverage report and open it"
	@echo "  test-coverage-rep        Show full line-by-line coverage report"
	@echo "  test-coverage-file       Show coverage for a specific file: FILE=..."
	@echo ""
	@echo "  check-updates            List outdated installed packages (informational)"
	@echo "  check-toml               Check pyproject.toml for syntax validity"
	@echo ""
	@echo "  clean-build              Remove build artifacts"
	@echo "  clean-logs               Remove logs (logs/DEV/*.log only)"
	@echo "  clean-cache              Remove cache files"
	@echo "  clean-coverage           Remove coverage data"
	@echo "  clean-pyc                Remove .pyc and __pycache__ files"
	@echo "  clean-all                Remove all build, test, cache, and log artifacts"
	@echo ""
	@echo "  build                    Build package for distribution"
	@echo ""
	@echo "  compose-up                 Start all services (detached)"
	@echo "  compose-down               Stop all services (keep volumes)"
	@echo "  compose-down-v             Stop all services and remove volumes (fresh DB)"
	@echo "  compose-ps               Show running containers and status"
	@echo "  compose-logs             Tail logs for all services (SERVICE=backend)"
	@echo "  compose-restart          Restart all services"
	@echo "  compose-build            Rebuild all service images"
	@echo "  compose-pull             Pull latest base images"
	@echo ""
	@echo ""
	@echo "  k8s-apply                Deploy via Helm (HELM_ENV=dev|qa|production; HELM_VALUES=...)"
	@echo "  k8s-delete               Uninstall the Helm release (HELM_ENV=dev|qa|production)"
	@echo "  k8s-build                Render Helm manifests (HELM_ENV=dev|qa|production)"
	@echo "  k8s-status               Check rollout status (HELM_ENV=dev|qa|production)"
	@echo "  k8s-pods                 List pods (HELM_ENV=dev|qa|production)"
	@echo "  k8s-services             List services (HELM_ENV=dev|qa|production)"
	@echo "  k8s-port-forward         Port-forward backend (HELM_ENV=dev|qa|production, PORT=8000)"
	@echo "  k8s-port-forward-all     Port-forward ALL services (HELM_ENV=dev|qa|production)"
	@echo "  k8s-wait                 Wait for services to become Ready (HELM_ENV=...)"
	@echo "  k8s-logs                 Aggregate/tail logs across services (HELM_ENV=...)"
	@echo "  deploy                   Full deploy + in-cluster verify gate (scripts/e2e.sh)"
	@echo "  rebuild-and-roll         Rebuild each image, roll it, health-check (HELM_ENV=...)"
	@echo "  smoke-test               HTTP smoke-test the live surface (SMOKE_BASE_URL=...)"
	@echo "  secrets                  Create/update a namespace Secret from .env (HELM_ENV=...)"
	@echo "  db-backup                On-demand PostgreSQL backup (HELM_ENV=...)"
	@echo "  certs                    Wire local self-signed TLS via cert-manager (HELM_ENV=...)"
	@echo "  (HELM_ARGS=... passes extra flags through to the operational scripts)"
	@echo "  kind-up                  Create (or reuse) the local kind cluster fst (KIND_NODE=floor|newest)"
	@echo "  kind-deploy              The kind job: images, bootstrap, ct, the release, helm test (KIND_BASE=<ref>)"
	@echo "  kind-down                Delete the kind cluster fst and .local/kind"
	@echo "  kind-shutdown-test       Delete a backend and a frontend Pod under load on fst: no request may fail"
	@echo "  kind-migration-test      The migration lifecycle on fst (install, upgrade, rollbacks, failures, TLS)"
	@echo "  kind-deploy-env-test     scripts/ci/deploy-env.sh itself on fst: install, upgrade, rollbacks, failed first install"
	@echo "  kind-preview-test        Pull request previews on fst: two deployed, one redeployed, both cleaned up, the deployer fenced"
	@echo "  kind-admission-test      The image admission policy on fst: signed and attested admitted, the rest denied"
	@echo "  kind-tracing-redis-test  Spans to a collector and the rate limit in Redis, through the NetworkPolicy, on fst"
	@echo "  kind-csp-report-test     A browser CSP violation on the release reaches the backend's log (Playwright)"
	@echo "  kind-live                The live suites against the release on fst (port-forwards as the deployer)"
	@echo "  kind-smoke               The smoke test against the release's public forward on fst"
	@echo "  kind-e2e                 The frontend's Playwright suite against the release's public forward on fst"
	@echo "  kind-zap                 A ZAP baseline against the release's public forward on fst (advisory)"
	@echo ""
	@echo "Supply-chain security (wraps infra/security/local/devsec.sh; extra flags via SEC_ARGS=...):"
	@echo "  security-local           Run the full chain (bootstrap + build + all)"
	@echo "  security-build           Build the images locally + emit scan.env"
	@echo "  security-tools           Check / provision local security tooling"
	@echo "  security-<step>          sbom | vuln | license | malware | iac | k8s | dast | vex | audit"
	@echo "  security-sbom-quality    Score SBOM quality + NTIA compliance (sbomqs, report-only)"
	@echo "  security-sbom-validate   Validate the SBOM schema (CycloneDX sbom-utility, report-only)"
	@echo "  security-publish         Push the SBOM to the registry (protected refs)"
	@echo "  security-clean           Remove local security artifacts"
	@echo ""
	@echo "Reporting (read-only views over output/security JSON — no scanner, wraps report.sh):"
	@echo "  security-report          One screen: SBOM + vuln + osv + VEX + license"
	@echo "  security-report-sbom|vuln|osv|vex|license   One section each"
	@echo "  security-report-origin MODE=tsv   Raw rows for the triage/SLA loop"
	@echo "  security-scan-search PATTERN=<re>  Grep a pattern across the JSON outputs"

install:
	$(UV) sync --locked --no-dev

develop:
	$(UV) sync --locked

lock:
	$(UV) lock

lock-check:
	$(UV) lock --check
# -------------------------------------------------------------------
# Environment-variable reference (Settings -> wiki/environment-variables.md)
# -------------------------------------------------------------------
# The backend-settings block of the doc is generated from the Settings model, so the doc cannot
# drift from the code; everything else in that file is hand-written.
env-docs:
	$(PYTHON) scripts/gen_env_docs.py

env-docs-check:
	$(PYTHON) scripts/gen_env_docs.py --check

# -------------------------------------------------------------------
# Develop Tools (Including Code Quality & Security Checks)
# -------------------------------------------------------------------
# One scope for format, lint and the pre-commit hook: all the Python the repo owns.
RUFF_PATHS := src/ tests/ scripts/ infra/ .github/scripts/

ruff-format:
	ruff format $(RUFF_PATHS)

ruff-format-check:
	ruff format --check $(RUFF_PATHS)

ruff-lint:
	ruff check --no-fix $(RUFF_PATHS)

# Auto-fixing lint, used only by check-all and infra/adapt/adopt.sh (never by CI, check-all-dry or pre-commit).
ruff-lint-fix:
	ruff check --fix $(RUFF_PATHS)

mypy-check:
	mypy

# -------------------------------------------------------------------
# OpenAPI single source (backend -> committed snapshot -> frontend typed client)
# -------------------------------------------------------------------
# The committed frontend/lib/api/openapi.json is a *generated artifact* — a snapshot of
# FastAPI's app.openapi() for the assembled backend, which is the one true source of the
# API contract. Regenerating it (then the TypeScript types) is the only supported way to
# refresh it, so the frontend client can never silently drift from what the backend serves.
openapi-gen:
	$(PYTHON) -m your_service_name.backend.openapi dump --output frontend/lib/api/openapi.json
	cd frontend && npm run openapi-gen

# Local mirror of the blocking CI `openapi-drift` job: regenerate the snapshot from the
# backend and the types from the snapshot, and fail if either committed file is stale.
openapi-drift-check:
	@tmp=$$(mktemp -d); \
	$(PYTHON) -m your_service_name.backend.openapi dump --output "$$tmp/openapi.json"; \
	if ! diff -u frontend/lib/api/openapi.json "$$tmp/openapi.json"; then \
		echo "OpenAPI drift: frontend/lib/api/openapi.json is out of date." >&2; \
		echo "Run 'make openapi-gen' to regenerate it from the backend." >&2; exit 1; \
	fi; \
	cd frontend; \
	node_modules/.bin/openapi-typescript ./lib/api/openapi.json --output "$$tmp/types.gen.d.ts" >/dev/null 2>&1; \
	if ! diff -u lib/api/types.gen.d.ts "$$tmp/types.gen.d.ts"; then \
		echo "OpenAPI drift: lib/api/types.gen.d.ts is out of date." >&2; \
		echo "Run 'npm run openapi-gen' to regenerate it." >&2; exit 1; \
	fi; \
	echo "OpenAPI snapshot and generated types are in sync with the backend."

# -------------------------------------------------------------------
# Shell script quality (shellcheck)
# -------------------------------------------------------------------
# Local mirror of the blocking `shellcheck` step of the `repo` job in ci.yml:
# lint every git-tracked *.sh at shellcheck's DEFAULT level (error + warning +
# info + style — the gate is green only when ALL scripts are clean) plus every
# *.bats parsed as bash. Same discovery (git ls-files) and the same verdict as
# CI, so a green local run means the CI gate is green too. `shellcheck` must be
# on PATH (it is pinned in CI; install locally via your package manager or the
# VS Code extension). Override the binary with SHELLCHECK=/path/to/shellcheck.
SHELLCHECK ?= shellcheck

# Build the images with throwaway tags and run the structure checks CI runs on a pull request (strict),
# then remove them. IDS="backend frontend" limits it. Needs docker, jq and the project venv.
image-checks:
	bash scripts/ci/image-checks-local.sh $(IDS)

# Build the frontend image with a dotted VERSION and a commit, run it, and check the page carries the sanitised
# deployment id. Throwaway tag and container, removed by exact name. Needs docker and curl.
frontend-deployment-id:
	bash scripts/ci/frontend-deployment-id-check.sh

shellcheck:
	@command -v "$(SHELLCHECK)" >/dev/null 2>&1 || { echo "shellcheck not found on PATH — install it (e.g. 'apk add shellcheck', 'brew install shellcheck', or the VS Code extension) to run this gate locally." >&2; exit 1; }
	@bash -c 'set -euo pipefail; \
	  mapfile -t SH   < <(git ls-files "*.sh"); \
	  mapfile -t BATS < <(git ls-files "*.bats"); \
	  if (( $${#SH[@]} + $${#BATS[@]} == 0 )); then echo "no tracked shell scripts to lint" >&2; exit 1; fi; \
	  echo "shellchecking $${#SH[@]} *.sh and $${#BATS[@]} *.bats (default level; .bats parsed as bash)"; \
	  if (( $${#SH[@]} > 0 ));   then $(SHELLCHECK) "$${SH[@]}";   fi; \
	  if (( $${#BATS[@]} > 0 )); then $(SHELLCHECK) -s bash "$${BATS[@]}"; fi'

# Every file link and in-repo #anchor in the tracked Markdown, offline (the repo job in ci.yml runs this target).
docs-links:
	@test -x .tools/bin/lychee || { echo "lychee not found; run: scripts/tools.sh install lychee" >&2; exit 1; }
	@bash -c 'set -euo pipefail; \
	  mapfile -t DOCS < <(git ls-files "*.md"); \
	  if (( $${#DOCS[@]} == 0 )); then echo "no tracked Markdown to check" >&2; exit 1; fi; \
	  .tools/bin/lychee --config lychee.toml -- "$${DOCS[@]}"'

# Pinned, checksum-verified dev toolchain in ./.tools (see tools/pins.tsv, scripts/tools.sh).
# Put it first on PATH so every target uses the pinned tools when they are installed; helm finds
# the pinned helm-unittest plugin through HELM_PLUGINS.
export PATH := $(CURDIR)/.tools/bin:$(PATH)
export HELM_PLUGINS := $(CURDIR)/.tools/helm-plugins

tools:
	@scripts/tools.sh install
	@scripts/tools.sh install crd-sources

tools-dry-run:
	@scripts/tools.sh --dry-run install

# Write the vendor-published checksums into tools/pins.tsv (and regenerate the hashed locks) after a version bump.
pins-refresh:
	@scripts/pins-refresh.sh

dev-tools-list:
	@echo "Dev tools from pyproject.toml ([dependency-groups] dev):"
	@printf "  - %s\n" $(DEV_TOOLS)

version-check-:
	@echo "Usage: make version-check-<package>"

# Read-only. Membership is checked against uv.lock alone (no network); a failed index query
# (offline, rate limit) is reported as "could not check" and does not fail the target.
version-check-%:
	@echo "Checking $* for updates..."
	@if [ -z "$$($(UV) tree --frozen --depth 0 --package $* 2>/dev/null)" ]; then \
		echo "  $(ESC)[31mERROR: '$*' is not in uv.lock$(ESC)[0m"; \
		exit 1; \
	fi; \
	if out="$$($(UV) tree --outdated --depth 0 --frozen --package $* 2>/dev/null)"; then \
		case "$$out" in \
			*latest:*) echo "  $(ESC)[33mNEW VERSION AVAILABLE$(ESC)[0m" ;; \
			*) echo "  $(ESC)[32mUp to date$(ESC)[0m" ;; \
		esac; \
	else \
		echo "  $(ESC)[33mcould not check (offline?)$(ESC)[0m"; \
	fi

dev-tools-outdated:
	@echo "Checking dev tools for available updates..."
	@rc=0; for P in $(DEV_TOOLS); do \
		$(MAKE) --no-print-directory version-check-$$P || rc=1; \
	done; exit $$rc

# Re-lock only the dev tools (newest release at least 7 days old), then install the result.
dev-tools-upgrade:
	$(UV) lock $(foreach p,$(DEV_TOOLS),--upgrade-package $(p))
	$(UV) sync --locked

check-all: dev-tools-outdated ruff-format ruff-lint-fix mypy-check env-docs
check-all-dry: dev-tools-outdated ruff-format-check ruff-lint mypy-check env-docs-check

# The hooks in .pre-commit-config.yaml, run by the pinned pre-commit (tools/pins.tsv).
precommit:
	@test -x .tools/bin/pre-commit || { echo "pre-commit not found; run: scripts/tools.sh install pre-commit" >&2; exit 1; }
	.tools/bin/pre-commit run --all-files

# The chart's helm-unittest suites (charts/your-service-name/tests), run by the pinned plugin
# (tools/pins.tsv; scripts/tools.sh install helm-unittest; found through HELM_PLUGINS above).
chart-unittest:
	helm unittest charts/your-service-name

# Every render of the chart (each environment x edge mode x monitoring flavour, every optional object,
# the Cloud SQL proxy) and of the admin bootstrap, through kubeconform -strict against local schemas
# only: no network. Needs scripts/tools.sh install helm4 kubeconform yq k8s-openapi.
kubeconform:
	scripts/ci/kubeconform.sh

# The chart's rules in both monitoring flavours through the pinned promtool: `promtool check rules` on
# the rendered groups and `promtool test rules` on charts/your-service-name/tests/rules. Offline.
# Needs scripts/tools.sh install helm4 promtool yq.
rules-test:
	scripts/ci/rules-test.sh

# The vendored CRD schemas (charts/your-service-name/ci/crds), generated from the pinned upstream CRDs
# in ci/crds/sources.tsv. The fetch (scripts/tools.sh install crd-sources, also part of `make tools`) is the only
# network step (sha256-checked, cached in .tools/crd-sources).
crd-schemas:
	scripts/tools.sh install crd-sources
	$(PYTHON) -I scripts/ci/kube_schemas.py crds generate

crd-schemas-check:
	scripts/tools.sh install crd-sources
	$(PYTHON) -I scripts/ci/kube_schemas.py crds check

# -------------------------------------------------------------------
# Tests
# -------------------------------------------------------------------
# Run every hermetic layer in one pass: unit + integration + e2e. No test uses the network: the
# ones that need a pinned artifact (helm, the Node tarball, the CRD sources) read what `make tools`
# downloaded and verified into .tools; the Node and CRD tests fail, naming the install, without it.
# Live tests are excluded on purpose (-m "not live") — they need a running
# external backend and have their own `test-live` target. Resetting addopts
# clears the default "not integration and not e2e and not live" marker filter
# so the integration/e2e layers actually run (unit tests are unmarked).
test:
	$(PYTHON) -m pytest tests/ -o addopts="" -m "not live and not contract" -v

# Unit layer only — the historical `test` behaviour AND the coverage gate.
# Relies on the default addopts marker filter ("not integration and not e2e and
# not live"); `--cov` activates measurement over [tool.coverage.run] source, and
# the 85% gate comes from [tool.coverage.report] fail_under — so a green local
# `test-unit` means the CI coverage gate is green too.
test-unit:
	$(PYTHON) -m pytest --cov --cov-report=term --cov-report=xml --junitxml=test-results/junit-unit.xml

# INTEGRATION_JUNIT lets CI keep the SQLite and PostgreSQL runs' reports apart.
INTEGRATION_JUNIT ?= test-results/junit-integration.xml
test-integration:
	$(PYTHON) -m pytest tests/ -m integration -o addopts="" -v --junitxml=$(INTEGRATION_JUNIT)

test-e2e:
	$(PYTHON) -m pytest tests/ -m e2e -o addopts="" -v --junitxml=test-results/junit-e2e.xml

# Schemathesis property tests from the OpenAPI document, in process. Not part of `test`: they use the
# database selected by TEST_DATABASE_URL (PostgreSQL in CI; see wiki/testing.md).
test-contract:
	$(PYTHON) -m pytest tests/contract -m contract -o addopts="" -v --junitxml=test-results/junit-contract.xml

test-live:
	$(PYTHON) -m pytest tests/ -m live -o addopts="" -v

test-file:
	@$(PYTHON) -c "import sys; f = '$(FILE)'; sys.exit(0) if f else (print('Usage: make test-file FILE=path/to/file.py'), sys.exit(1))"
	$(PYTHON) -m pytest $(FILE) -v

test-file-function:
	@$(PYTHON) -c "import sys; f = '$(FILE)'; func = '$(FUNC)'; sys.exit(0) if f and func else (print('Usage: make test-file-function FILE=path/to/file.py FUNC=function_name'), sys.exit(1))"
	$(PYTHON) -m pytest $(FILE)::$(FUNC) -v

test-fast:
	$(PYTHON) -m pytest --lf -x -v

# -------------------------------------------------------------------
# Coverage
# -------------------------------------------------------------------
# Scope ([tool.coverage.run] source) and the 85% gate ([tool.coverage.report]
# fail_under) are single-sourced in pyproject, so these only ACTIVATE coverage
# with a bare --cov and pick the report format — no restated path or threshold.
test-coverage:
	$(PYTHON) -m pytest --cov --cov-report=term

test-coverage-xml:
	$(PYTHON) -m pytest --cov --cov-report=term --cov-report=xml

test-cov-html:
	$(PYTHON) -m pytest --cov --cov-report=html
	$(PYTHON) -c "import webbrowser; webbrowser.open('htmlcov/index.html')"

test-coverage-rep:
	coverage report -m

test-coverage-file:
	@$(PYTHON) -c "import sys; f = '$(FILE)'; sys.exit(0) if f else (print('Usage: make test-coverage-file FILE=path/to/file.py'), sys.exit(1))"
	coverage report -m $(FILE)

# -------------------------------------------------------------------
# Clean
# -------------------------------------------------------------------
clean-build:
	@echo "Removing build artifacts..."
	$(PYTHON) -c "import shutil, pathlib; [shutil.rmtree(p, ignore_errors=True) for p in map(pathlib.Path, ['dist', 'build']) if p.exists()]; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').rglob('*.egg-info')]"

check-updates:
	$(UV) tree --outdated --depth 1 --frozen

check-toml:
	@$(PYTHON) -c "import tomllib; tomllib.load(open('pyproject.toml', 'rb')); print('pyproject.toml syntax is valid')"

clean-logs:
	@echo "Removing DEV log files..."
	$(PYTHON) -c "import pathlib; [p.unlink() for p in pathlib.Path('logs/DEV').rglob('*.log')]"

clean-cache:
	@echo "Removing cache files..."
	$(PYTHON) -c "import pathlib, shutil; [shutil.rmtree(p, ignore_errors=True) for p in map(pathlib.Path, ['.pytest_cache', '.mypy_cache', '.ruff_cache', 'htmlcov']) if p.exists()]; cache = pathlib.Path('.cache'); [shutil.rmtree(p, ignore_errors=True) for p in cache.iterdir() if p.is_dir()] if cache.exists() else None"

clean-coverage:
	@echo "Removing coverage data..."
	$(PYTHON) -c "import pathlib; [p.unlink() for p in map(pathlib.Path, ['.coverage', 'coverage.xml']) if p.exists()]"
	coverage erase

clean-pyc:
	@echo "Removing .pyc and __pycache__ files..."
	$(PYTHON) -c "import pathlib, shutil; [p.unlink() for p in pathlib.Path('.').rglob('*.pyc')]; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').rglob('__pycache__')]"

clean-all: clean-build clean-logs clean-cache clean-coverage clean-pyc
	@echo ""
	@echo "All build, test, log, and cache artifacts have been removed."

# -------------------------------------------------------------------
# Build
# -------------------------------------------------------------------
build:
	$(PYTHON) -m build

# For tools that read a requirements file: the locked runtime set with sha256 hashes, no editable project
# line, no header (the header would print the command line). The file is not tracked (.gitignore).
REQUIREMENTS ?= requirements.txt
export-requirements:
	$(UV) export --frozen --no-dev --no-editable --no-emit-project --no-header --format requirements-txt --output-file $(REQUIREMENTS)
	@echo "Done: $(REQUIREMENTS) written from uv.lock."

check-requirements-sync:
	$(UV) lock --check

# -------------------------------------------------------------------
# Docker Compose
# -------------------------------------------------------------------
compose-up:
	docker compose up -d

compose-down:
	docker compose down

compose-down-v:
	docker compose down -v

compose-ps:
	docker compose ps

compose-logs:
ifdef SERVICE
	docker compose logs -f $(SERVICE)
else
	docker compose logs -f
endif

compose-restart:
	docker compose restart

compose-build:
	docker compose build

compose-build-backend:
	docker build -t your-service-name-backend:local -f Dockerfile.backend .

compose-build-frontend:
	docker build -t your-service-name-frontend:local -f Dockerfile.frontend .

compose-pull:
	docker compose pull

# -------------------------------------------------------------------
# Kubernetes (Helm)
# -------------------------------------------------------------------
# These drive the Helm chart (charts/your-service-name) through the operational
# scripts — helm upgrade --install / uninstall via scripts/k8s-env.sh — instead of
# the retired Kustomize tree. HELM_ENV selects charts/your-service-name/values.<HELM_ENV>.yaml.
# Default environment is 'dev' if HELM_ENV is not given. Override the release name with
# HELM_RELEASE and pass an extra values file with HELM_VALUES.
HELM_ENV ?= dev
HELM_RELEASE ?= your-service-name
HELM_VALUES ?=
K8S_PORT ?= 8000
# HELM_ARGS is a pass-through for extra flags on the operational scripts
# (e.g. `make deploy HELM_ARGS=--only backend,frontend`); SMOKE_BASE_URL is the
# HTTP base the smoke-test target probes. Both default so a bare `make <target>`
# works, exactly like HELM_ENV / K8S_PORT above.
HELM_ARGS ?=
SMOKE_BASE_URL ?= http://localhost:8000
# Host header to send (a dev/qa/production backend checks it; use <release>-backend). Empty: none.
SMOKE_HOST ?=

k8s-apply:
	@echo "Applying $(HELM_ENV) environment (release $(HELM_RELEASE))..."
	HELM_RELEASE="$(HELM_RELEASE)" ./scripts/k8s-env.sh apply --env $(HELM_ENV) $(if $(HELM_VALUES),--values $(HELM_VALUES),)

k8s-delete:
	@echo "Deleting $(HELM_ENV) environment (release $(HELM_RELEASE))..."
	HELM_RELEASE="$(HELM_RELEASE)" ./scripts/k8s-env.sh delete --env $(HELM_ENV)

k8s-build:
	@echo "Rendering $(HELM_ENV) manifests..."
	helm template $(HELM_RELEASE) charts/your-service-name -f charts/your-service-name/values.$(HELM_ENV).yaml

k8s-status:
	@echo "Checking rollout status for $(HELM_ENV)..."
	HELM_RELEASE="$(HELM_RELEASE)" ./scripts/k8s-env.sh status --env $(HELM_ENV)

k8s-pods:
	kubectl -n your-service-name-$(HELM_ENV) get pods

k8s-services:
	kubectl -n your-service-name-$(HELM_ENV) get services

k8s-port-forward:
	@echo "Port-forwarding backend on port $(K8S_PORT)..."
	HELM_RELEASE="$(HELM_RELEASE)" ./scripts/port-forward.sh --env $(HELM_ENV) --only backend --port backend=$(K8S_PORT)

# The full local-dev workflow beyond apply/status: the outer `deploy` gate
# (scripts/e2e.sh: secrets → apply → wait → in-cluster verify), rebuild+roll
# after a code change, all-service port-forward, live logs, the HTTP smoke
# gate, and the specialised helpers. Each drives the Helm chart through its
# operational script (nothing hard-coded; namespace/tag from values.$(HELM_ENV).yaml).
deploy:
	@echo "Deploying + verifying $(HELM_ENV) environment (release $(HELM_RELEASE))…"
	HELM_RELEASE="$(HELM_RELEASE)" ./scripts/e2e.sh deploy --env $(HELM_ENV) $(HELM_ARGS)

k8s-wait:
	@echo "Waiting for $(HELM_ENV) services to become Ready…"
	HELM_RELEASE="$(HELM_RELEASE)" ./scripts/k8s-env.sh wait --env $(HELM_ENV) $(HELM_ARGS)

k8s-logs:
	@echo "Aggregating $(HELM_ENV) logs (Ctrl-C to stop)…"
	HELM_RELEASE="$(HELM_RELEASE)" ./scripts/logs.sh --env $(HELM_ENV) $(HELM_ARGS)

# k8s-port-forward forwards only the backend; this opens every service.
k8s-port-forward-all:
	@echo "Port-forwarding all $(HELM_ENV) services (Ctrl-C to stop)…"
	HELM_RELEASE="$(HELM_RELEASE)" ./scripts/port-forward.sh --env $(HELM_ENV) $(HELM_ARGS)

rebuild-and-roll:
	@echo "Rebuilding + rolling $(HELM_ENV) services…"
	HELM_RELEASE="$(HELM_RELEASE)" ./scripts/rebuild-and-roll.sh --env $(HELM_ENV) $(HELM_ARGS)

smoke-test:
	@echo "Smoke-testing the live HTTP surface at $(SMOKE_BASE_URL)…"
	./scripts/smoke-test.sh --base-url $(SMOKE_BASE_URL) $(if $(SMOKE_HOST),--host $(SMOKE_HOST)) $(HELM_ARGS)

secrets:
	@echo "Updating $(HELM_ENV) secrets from .env (keys only; values masked)…"
	HELM_RELEASE="$(HELM_RELEASE)" ./scripts/secrets.sh update --env $(HELM_ENV) -f .env $(HELM_ARGS)

db-backup:
	@echo "Taking an on-demand $(HELM_ENV) PostgreSQL backup…"
	./scripts/db-backup.sh --env $(HELM_ENV) $(HELM_ARGS)

# The local kind cluster fst (always --name fst, kubeconfig .local/kind/kubeconfig) and the kind job's
# sequence, the one CI runs (wiki/infrastructure.md, Local cluster). KIND_NODE picks the pinned node
# image (tools/kind-nodes.tsv); KIND_BASE=<ref> adds the upgrade test from that commit's chart
# (a commit id, branch or tag; no ~ or ^).
KIND_NODE ?= floor
KIND_BASE ?=

kind-up:
	scripts/kind-up.sh --node $(KIND_NODE)

kind-deploy:
	scripts/kind-deploy.sh $(if $(KIND_BASE),--base $(KIND_BASE))

kind-down:
	scripts/kind-up.sh --delete

# After kind-deploy: tests/k8s/test_graceful_shutdown.py (make test never collects tests/k8s).
kind-shutdown-test:
	$(PYTHON) -m pytest tests/k8s/test_graceful_shutdown.py -o addopts="" -v -s

# After kind-deploy: tests/k8s/test_migrations.py, the migration lifecycle on a release of its own
# (one sequence, so it stops at the first failure).
kind-migration-test:
	$(PYTHON) -m pytest tests/k8s/test_migrations.py -o addopts="" -v -s -x

# After kind-deploy: tests/k8s/test_deploy_env.py, scripts/ci/deploy-env.sh itself on a release of its
# own, its images by digest from a throwaway registry (one sequence, so it stops at the first failure).
kind-deploy-env-test:
	$(PYTHON) -m pytest tests/k8s/test_deploy_env.py -o addopts="" -v -s -x

# After kind-deploy: tests/k8s/test_previews.py, pull request previews (the preview bootstrap, deploy-env.sh
# with PREVIEW_PR, preview-cleanup.sh) as the preview deployer, in namespaces of its own (one sequence).
kind-preview-test:
	$(PYTHON) -m pytest tests/k8s/test_previews.py -o addopts="" -v -s -x

# After kind-up: tests/k8s/test_admission.py, the image admission policy with the pinned Kyverno,
# ephemeral keys and a throwaway registry, all of its own (wiki/admission-control.md, Proven on kind).
kind-admission-test:
	$(PYTHON) -m pytest tests/k8s/test_admission.py -o addopts="" -v -s

# After kind-deploy: tests/k8s/test_tracing_redis.py, on a release of its own with a throwaway
# collector and Redis (wiki/backend-architecture.md, Tracing; wiki/security.md, Backend Rate Limiting).
kind-tracing-redis-test:
	$(PYTHON) -m pytest tests/k8s/test_tracing_redis.py -o addopts="" -v -s

# After kind-deploy and the frontend's Playwright install: tests/k8s/test_csp_report.py (it runs
# frontend/e2e/csp-report.spec.ts through scripts/ci/kind-forward.sh; wiki/security.md, CSP Violation Reports).
kind-csp-report-test:
	$(PYTHON) -m pytest tests/k8s/test_csp_report.py -o addopts="" -v -s

# After kind-deploy: the suites against the release, through scripts/ci/kind-forward.sh (it starts the
# port-forwards, exports the URLs, runs the command, stops them). kind-zap is advisory: findings exit 0.
kind-live:
	scripts/ci/kind-forward.sh $(MAKE) test-live

kind-smoke:
	scripts/ci/kind-forward.sh $(MAKE) smoke-test

kind-e2e:
	cd frontend && ../scripts/ci/kind-forward.sh npm run test:e2e

kind-zap:
	DAST_TARGET_RUNNING=true DAST_ZAP_CONTAINER="fst-zap-$$(date +%s)-$$$$" \
	  scripts/ci/kind-forward.sh bash infra/security/local/scan-dast.sh

certs:
	@echo "Wiring local self-signed TLS for $(HELM_ENV) (cert-manager)…"
	./scripts/certs-local.sh --env $(HELM_ENV) $(HELM_ARGS)

# -------------------------------------------------------------------
# Supply-chain security — local runner
# -------------------------------------------------------------------
# Local stand-in for the CI security workflows. `devsec.sh` is the wrapper:
# it adapts the environment and invokes the single-source scan scripts under
# infra/security/local/ (scan-*.sh) — one script per step, shared verbatim by
# the CI job, `devsec.sh <step>`, and its bats test. `REPORT` is the read-only
# display layer over the scan JSON (runs no scanner). `SEC` is the wrapper;
# `SEC_ARGS` forwards extra flags to a step, e.g.
#   make security-vuln SEC_ARGS="--fail-on critical"
# Per-developer overrides (registry / project / credentials) live in the
# gitignored .env.local beside the wrapper — never committed.
SEC := infra/security/local/devsec.sh
REPORT := infra/security/local/report.sh
SEC_ARGS ?=

# --- Chain / build / tooling ---
security-local:
	$(SEC) bootstrap
	$(SEC) build
	$(SEC) all

security-build:
	$(SEC) build

security-tools:
	$(SEC) tools

# --- Scan steps ---
# sbom/vuln/license are the supply-chain chain; iac/k8s/dast are the appsec/IaC
# scanners (all advisory / report-only — a finding is a red in the report, never
# a block). No input present → an honest SKIPPED.
security-sbom:
	$(SEC) sbom $(SEC_ARGS)

security-vuln:
	$(SEC) vuln $(SEC_ARGS)

security-license:
	$(SEC) license $(SEC_ARGS)

security-malware:
	$(SEC) malware-heuristic $(SEC_ARGS)

security-iac:
	$(SEC) iac $(SEC_ARGS)

security-k8s:
	$(SEC) k8s $(SEC_ARGS)

security-dast:
	$(SEC) dast $(SEC_ARGS)

security-vex:
	$(SEC) vex $(SEC_ARGS)

security-audit:
	$(SEC) audit $(SEC_ARGS)

# --- SBOM analysis — report-only quality + validation over the SBOM the chain produced ---
security-sbom-quality:
	$(SEC) sbomqs $(SEC_ARGS)

security-sbom-validate:
	$(SEC) sbom-utility $(SEC_ARGS)

# --- Reporting — human-readable views over the scan JSON (report.sh, read-only) ---
security-report:
	@bash $(REPORT) summary

security-report-sbom:
	@bash $(REPORT) sbom

security-report-vuln:
	@bash $(REPORT) vuln

security-report-osv:
	@bash $(REPORT) osv

security-report-vex:
	@bash $(REPORT) vex

security-report-license:
	@bash $(REPORT) license

security-report-origin:
	@bash $(REPORT) origin "$(MODE)"

security-scan-search:
	@bash $(REPORT) search "$(PATTERN)"

# --- Publish / clean ---
security-publish:
	$(SEC) publish

security-clean:
	$(SEC) clean