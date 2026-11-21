# your-service-name — adaptable service template (see wiki/getting-started.md)
.PHONY: all help install develop \
    ruff-format ruff-format-check ruff-lint ruff-lint-fix mypy-check \
    openapi-gen openapi-drift-check \
    shellcheck tools tools-dry-run pins-refresh \
    check-all check-all-dry precommit \
    test test-unit test-integration test-e2e test-live test-file test-file-function test-fast \
    test-coverage test-coverage-xml test-cov-html test-coverage-rep test-coverage-file \
    check-updates check-toml \
    clean clean-build clean-logs clean-cache clean-coverage clean-pyc clean-all \
    build \
    compose-up compose-down compose-down-v compose-ps compose-logs compose-restart \
    compose-build compose-pull \
    export-requirements check-requirements-sync \
    dev-tools-list dev-tools-outdated dev-tools-upgrade version-check- \
    k8s-apply k8s-delete k8s-build k8s-status k8s-pods k8s-services k8s-port-forward \
    k8s-wait k8s-logs k8s-port-forward-all deploy rebuild-and-roll smoke-test \
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
PIP := $(PYTHON) -m pip
SERVICE := your_service_name
# COV_FAIL_UNDER intentionally absent: the 85% gate is single-sourced in
# pyproject [tool.coverage.report] fail_under, so any `--cov` run enforces it.

# Auto-extract dev tools from pyproject.toml (names only; strip pins/extras)
ESC := $(shell printf '\033')
DEV_TOOLS := $(shell \
    $(PYTHON) -c "import re,tomllib,pathlib; d=tomllib.loads(pathlib.Path('pyproject.toml').read_text()); dev=d.get('project',{}).get('optional-dependencies',{}).get('develop',[]); names=[]; [names.append(re.split(r'[<>=!~\\[]', s, maxsplit=1)[0].strip()) for s in dev]; print(' '.join(sorted(set(filter(None,names)))))" \
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
	@echo "  install                  Install the package in editable mode (for production)"
	@echo "  develop                  Install with all develop dependencies"
	@echo ""
	@echo "  ruff-format              Auto-format code using Ruff"
	@echo "  ruff-format-check        Check code formatting (dry run)"
	@echo "  ruff-lint                Run Ruff linter"
	@echo "  mypy-check               Run MyPy static type checker"
	@echo "  shellcheck               Lint all tracked shell scripts (*.sh + *.bats) with ShellCheck"
	@echo "  check-all                Run dev tool drift check + format + lint + mypy"
	@echo "  check-all-dry            Same as above but without auto-format (format-check)"
	@echo "  precommit                Run every pre-commit hook on all files (pinned pre-commit, no network)"
	@echo "  openapi-gen              Regenerate openapi.json + TS types from the backend single source"
	@echo "  openapi-drift-check      Fail if committed openapi.json / types.gen.d.ts is stale (CI mirror)"
	@echo ""
	@echo "  dev-tools-list           Show dev tools detected from pyproject.toml"
	@echo "  dev-tools-outdated       Dry-run upgrade check for all dev tools"
	@echo "  dev-tools-upgrade        Upgrade all dev tools to latest (non-prod deps only)"
	@echo "  version-check-<pkg>      Check if an upgrade is available for a single package"
	@echo ""
	@echo "  test                     Run all hermetic test layers (unit + integration + e2e) using Pytest"
	@echo "  test-unit                Run only unit tests using Pytest"
	@echo "  test-integration         Run integration tests using Pytest"
	@echo "  test-e2e                 Run end-to-end tests using Pytest (-m e2e)"
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
	@echo "  export-requirements      Export requirements.txt from pyproject.toml (via Poetry)"
	@echo "  check-requirements-sync  Check if requirements.txt matches pyproject.toml (via Poetry)"
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
	$(PIP) install -e .

develop:
	$(PIP) install -e .[develop]
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

# Auto-fixing lint, used only by check-all (never by CI, check-all-dry or pre-commit).
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

shellcheck:
	@command -v "$(SHELLCHECK)" >/dev/null 2>&1 || { echo "shellcheck not found on PATH — install it (e.g. 'apk add shellcheck', 'brew install shellcheck', or the VS Code extension) to run this gate locally." >&2; exit 1; }
	@bash -c 'set -euo pipefail; \
	  mapfile -t SH   < <(git ls-files "*.sh"); \
	  mapfile -t BATS < <(git ls-files "*.bats"); \
	  if (( $${#SH[@]} + $${#BATS[@]} == 0 )); then echo "no tracked shell scripts to lint" >&2; exit 1; fi; \
	  echo "shellchecking $${#SH[@]} *.sh and $${#BATS[@]} *.bats (default level; .bats parsed as bash)"; \
	  if (( $${#SH[@]} > 0 ));   then $(SHELLCHECK) "$${SH[@]}";   fi; \
	  if (( $${#BATS[@]} > 0 )); then $(SHELLCHECK) -s bash "$${BATS[@]}"; fi'

# Pinned, checksum-verified dev toolchain in ./.tools (see tools/pins.tsv, scripts/tools.sh).
# Put it first on PATH so every target uses the pinned tools when they are installed; helm finds
# the pinned helm-unittest plugin through HELM_PLUGINS.
export PATH := $(CURDIR)/.tools/bin:$(PATH)
export HELM_PLUGINS := $(CURDIR)/.tools/helm-plugins

tools:
	@scripts/tools.sh install

tools-dry-run:
	@scripts/tools.sh --dry-run install

# Write the vendor-published checksums into tools/pins.tsv (and regenerate the hashed locks) after a version bump.
pins-refresh:
	@scripts/pins-refresh.sh

dev-tools-list:
	@echo "Dev tools from pyproject.toml (develop extra):"
	@printf "  - %s\n" $(DEV_TOOLS)

version-check-:
	@echo "Usage: make version-check-<package>"

version-check-%:
	@echo "Checking $* for updates..."
	@if ! $(PIP) show $* >/dev/null 2>&1; then \
		echo "  $(ESC)[31mERROR: '$*' is not installed$(ESC)[0m"; \
		exit 1; \
	fi
	@$(PIP) install --upgrade --dry-run $* 2>&1 \
		| grep -q "Would install" \
		&& echo "  $(ESC)[33mNEW VERSION AVAILABLE$(ESC)[0m" \
		|| echo "  $(ESC)[32mUp to date$(ESC)[0m"

dev-tools-outdated:
	@echo "Checking dev tools for available updates..."
	@for P in $(DEV_TOOLS); do \
		$(MAKE) --no-print-directory version-check-$$P ; \
    done

dev-tools-upgrade:
	$(PIP) install -U $(DEV_TOOLS)

check-all: dev-tools-outdated ruff-format ruff-lint-fix mypy-check
check-all-dry: dev-tools-outdated ruff-format-check ruff-lint mypy-check

# The hooks in .pre-commit-config.yaml, run by the pinned pre-commit (tools/pins.tsv).
precommit:
	@test -x .tools/bin/pre-commit || { echo "pre-commit not found; run: scripts/tools.sh install pre-commit" >&2; exit 1; }
	.tools/bin/pre-commit run --all-files

# -------------------------------------------------------------------
# Tests
# -------------------------------------------------------------------
# Run every hermetic layer in one pass: unit + integration + e2e.
# Live tests are excluded on purpose (-m "not live") — they need a running
# external backend and have their own `test-live` target. Resetting addopts
# clears the default "not integration and not e2e and not live" marker filter
# so the integration/e2e layers actually run (unit tests are unmarked).
test:
	$(PYTHON) -m pytest tests/ -o addopts="" -m "not live" -v

# Unit layer only — the historical `test` behaviour AND the coverage gate.
# Relies on the default addopts marker filter ("not integration and not e2e and
# not live"); `--cov` activates measurement over [tool.coverage.run] source, and
# the 85% gate comes from [tool.coverage.report] fail_under — so a green local
# `test-unit` means the CI coverage gate is green too.
test-unit:
	$(PYTHON) -m pytest --cov --cov-report=term --cov-report=xml --junitxml=test-results/junit-unit.xml

test-integration:
	$(PYTHON) -m pytest tests/ -m integration -o addopts="" -v --junitxml=test-results/junit-integration.xml

test-e2e:
	$(PYTHON) -m pytest tests/ -m e2e -o addopts="" -v --junitxml=test-results/junit-e2e.xml

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
	$(PIP) list --outdated

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
# Build & requirements (pip-compile)
# -------------------------------------------------------------------
build:
	$(PYTHON) -m build

export-requirements:
	@echo "Exporting requirements.txt from pyproject.toml..."
	pip-compile pyproject.toml --output-file requirements.txt --no-annotate --no-header
	@echo "Done: requirements.txt updated."

check-requirements-sync:
	@echo "Checking if requirements.txt is up-to-date..."
	pip-compile pyproject.toml --output-file .requirements.txt.check --no-annotate --no-header
	@$(PYTHON) -m difflib $(shell test -f requirements.txt && echo requirements.txt || echo /dev/null) .requirements.txt.check >/dev/null 2>&1 || true
	@$(PYTHON) -c "import sys; a=open('requirements.txt').read() if __import__('pathlib').Path('requirements.txt').exists() else ''; b=open('.requirements.txt.check').read(); sys.exit(0) if a == b else sys.exit(1)" || \
	    (echo "" && \
	    echo "requirements.txt is out of sync with pyproject.toml." && \
	    echo "   Run 'make export-requirements' to fix it." && exit 1)
	@$(PYTHON) -c "import os; os.remove('.requirements.txt.check')"

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

compose-build-nginx:
	docker build -t your-service-name-nginx:local -f Dockerfile.nginx .

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
# (e.g. `make deploy HELM_ARGS=--only backend,nginx`); SMOKE_BASE_URL is the
# HTTP base the smoke-test target probes. Both default so a bare `make <target>`
# works, exactly like HELM_ENV / K8S_PORT above.
HELM_ARGS ?=
SMOKE_BASE_URL ?= http://localhost:8000

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
	./scripts/smoke-test.sh --base-url $(SMOKE_BASE_URL) $(HELM_ARGS)

secrets:
	@echo "Updating $(HELM_ENV) secrets from .env (keys only; values masked)…"
	HELM_RELEASE="$(HELM_RELEASE)" ./scripts/secrets.sh update --env $(HELM_ENV) -f .env $(HELM_ARGS)

db-backup:
	@echo "Taking an on-demand $(HELM_ENV) PostgreSQL backup…"
	./scripts/db-backup.sh --env $(HELM_ENV) $(HELM_ARGS)

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