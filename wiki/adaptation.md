# Adaptation

How to rename the service using the adaptation script.

---

## Table of Contents

- [Overview](#overview)
- [Config-Driven Adoption (Kubernetes)](#config-driven-adoption-kubernetes-deployments)
- [Renaming the Service](#renaming-the-service)
- [Configuring Ports](#configuring-ports)
- [Script Details](#script-details)
- [Manual Adaptation](#manual-adaptation)
- [Verification](#verification)

---

## Overview

The template ships with a placeholder name (`your_service_name`). Before using it for a real
service, rename it to your actual service name.

The rename script has **dry-run mode** and **backup creation** for safety. Ports are configured
separately (environment-driven, no script) — see [Configuring Ports](#configuring-ports).

---

## Config-Driven Adoption (Kubernetes deployments)

For Kubernetes deployments the template is adapted in one config-driven pass from a single file,
[`infra/adapt/adopt.conf`](../infra/adapt/adopt.conf). It covers two independent axes:

- **Identity** — the service name → the Python package `src/<name>`, the image-name suffix, the
  k8s name prefix, `BACKEND_ENTRY`, and the CI `package_name`. Handled by `replace_service_name.py`.
- **Coordinates** — the ingress base domain and the image registry. Handled by `adopt_stamp.py`.

```bash
# 1. edit infra/adapt/adopt.conf (SERVICE_NAME, DOMAIN, REGISTRY, optionally REPOSITORY)
infra/adapt/adopt.sh --dry-run   # 2. preview — touches nothing
infra/adapt/adopt.sh             # 3. apply (backs up every touched file to .adapt-backup/<ts>/)
```

`adopt.sh` is a thin wrapper that runs the name adapter and then the stamping step — both read the
same `adopt.conf`, so one edit applies everywhere — forwarding `--dry-run`. The stamping replaces
`your-domain.example` / `registry.your-domain.example` in every file that carries them: the `Dockerfile*`,
`pyproject.toml`, the chart's `values.*.yaml` and `Chart.yaml`, and the conduct contact in `CODE_OF_CONDUCT.md`,
among others. The stamp, with the name, gives each environment one hostname, `<name>[-dev|-qa].<domain>` (production is `<name>.<domain>`); `edge.hosts.api` stays empty unless you want a backend-only host ([Infrastructure, edge hostnames](./infrastructure.md#edge-hostnames)). The optional `REPOSITORY` (`owner/name`) replaces `your-org/your-repo` in the `SOURCE_URL` default of
the three Dockerfiles (the image's OCI `source` label for local and kind builds; CI passes the real URL), the README
badges, and the links in `SECURITY.md`, the issue forms and the pull request template. The optional `CODEOWNER` (a user
`@name`, a team `@org/team` or an email; GitHub rejects a bare organisation) replaces `@your-org` in
`.github/CODEOWNERS`, and the adapter refuses another shape. Unset, those placeholders stay, and the adapter prints a
notice while `CODEOWNERS` still has its placeholder. The dry-run lists every file it would change.
It also writes `.template-version`, the template release this copy was made from (`v` and the version in
`.release-please-manifest.json`), once: a re-run keeps it. [Template upgrades](./template-upgrades.md) uses it.
Right after that first write it resets the manifest (and `.release-please-version`, if present) to `0.0.0`, so your first release starts your own line: `v0.1.0`, whatever the commit types, because release-please finds no previous release at `0.0.0` and uses the config's `initial-version` (`0.1.0`), instead of continuing the template's.

**Cluster structure is a separate, per-cluster layer.** The values that depend on *which cluster*
you run on — the ingress controller class, the StorageClass, the TLS mode, the CI short image
names, the pull secret — are not derivable from the name or domain, so the adapter does not set
them. Start from the committed skeleton `charts/your-service-name/values.cluster.yaml.example`,
copy it to `values.<cluster>.yaml`, and fill the `<ALL-CAPS>` blanks (`adopt.sh` has already
stamped your real domain + registry into the copy). `.gitignore` does not cover it: once it holds real values, add
it there or keep it out of the repository.
See [Deploying to Kubernetes](getting-started.md#deploying-to-kubernetes-production).

---

## Renaming the Service

### Script

```bash
infra/adapt/replace_service_name.sh
```

### Usage

```bash
# Dry-run (shows what would change, no files modified)
infra/adapt/replace_service_name.sh -n awesome_solution --dry-run

# Apply the rename (every write is backed up automatically to .adapt-backup/<timestamp>/)
infra/adapt/replace_service_name.sh -n awesome_solution
```

### What Gets Renamed

| Location | Before | After |
|----------|--------|-------|
| `src/your_service_name/` | `your_service_name` | `awesome_solution` |
| `pyproject.toml` | `name = "your_service_name"` | `name = "awesome_solution"` |
| `Dockerfile.backend` | `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_YOUR_SERVICE_NAME` | `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_AWESOME_SOLUTION` |
| Run scripts, entrypoint & CI (`BACKEND_ENTRY`) | `your_service_name.backend.main:app` | `awesome_solution.backend.main:app` |
| `Dockerfile.backend` | `your_service_name` references | `awesome_solution` references |
| `Dockerfile.frontend` | `your_service_name` references | `awesome_solution` references |
| All Python imports | `from your_service_name...` | `from awesome_solution...` |
| The display name (page title, image title labels) | `Your Service Name` | `Awesome Solution` |
| Any other mention (a path, a test's allow-list) | `your_service_name` / `your-service-name` | `awesome_solution` / `awesome-solution` |

### What the Adapters Stamp, and What They Leave

Both adapters walk every file outside [`infra/adapt/excludes.txt`](../infra/adapt/excludes.txt) whose type they
know, as [`infra/adapt/_common.py`](../infra/adapt/_common.py) lists them (`DEFAULT_INCLUDE_EXTS` and
`should_edit`): the code, scripts, config, docs, `uv.lock` and the chart's helm-unittest snapshots, and any of these
with an `.example` suffix. A file that is not UTF-8 text is skipped with a warning, so no binary file is ever edited, and a rewritten
script keeps its mode. The service-name forms are `your_service_name`, `your-service-name` and `Your Service Name`;
the stamping forms are `your-domain.example` and `registry.your-domain.example` (also with their dots escaped, as a
test's regex names them), `your-org/your-repo` and `YourOrg` (the image vendor labels and the chart maintainer, which
take the repository's owner) with `REPOSITORY`, and `@your-org` in `CODEOWNERS` with `CODEOWNER`.

A name or a domain of another length moves two things the text rewrite cannot: the padded columns of a rendered chart
snapshot, and where ruff sorts an import or wraps a Python line. So `adopt.sh` then renders the chart's helm-unittest
snapshots again with the pinned helm and plugin (a failing chart test stops it, printing helm-unittest's report and
the backup directory: the rename is already applied), and runs `make ruff-imports-fix ruff-format` with the project's
ruff (`.venv/bin/ruff`, or the `RUFF` variable), which sorts the imports and formats, and fixes nothing else; a ruff
failure is a warning naming that command. Either tool missing, it prints the command to run once it is installed.

The production OIDC issuer and audience are placeholders of their own, which the stamp never rewrites, so an adopted
copy's deploy guard still refuses each until you set your provider's issuer and the API's audience.

`excludes.txt` is the one list of what keeps the placeholder, each group with its reason; the leftover check
(`scripts/ci/check-placeholder-leftovers.sh`) allows exactly those paths:

- caches, tool installs and build output (`.venv`, `node_modules`, `.tools`, …): not the adopter's text;
- the adapter and its tests (`infra/adapt`, its two test files): they hold the forms they replace;
- the pages that explain the placeholder: `README.md`, `CONTRIBUTING.md`, `CONVENTIONS.md`, `AGENTS.md`, `agent/`, this
  page, [Template upgrades](./template-upgrades.md) (its steps rename an upstream merge), the generic-names decision
  record and the [changelog](./changelog.md), a record of the template's own releases.

Every other wiki page documents the adopter's service, so it is stamped like the code: its commands, names and links
follow the rename. A guard that must still recognise the template's placeholder after adoption (the chart's
`guards.rejectPlaceholders`, the bootstrap's runbook check, the leftover check) spells it in pieces, so the adapter never
turns it against the adopter's own values.

**The end-to-end check.** [`tests/scripts/adopt-e2e.bats`](../tests/scripts/adopt-e2e.bats) adopts a copy of the
working tree as `acme_orders` with every optional value set, and fails if any placeholder form is left outside the
list, if a listed path no longer holds one (a stale exclusion), if the chart directory or the package is not renamed,
if a file's mode or a binary file changed, or if a second run changes anything. The `Adopted copy` CI job runs it,
then the copy's own suites ([CI/CD](./ci-cd.md#the-adopted-copy)). Both check the template only: in an adopted
repository the test skips, and the job succeeds at once with a notice, one short step. Keep both there: the copy's own
supply-chain tests read the job and the test.

---

## Configuring Ports

There is no port-adaptation script: the retired HAProxy edge removed the single "public" port, and
the remaining ports are environment-driven. Change them by editing the relevant files directly
(dry-run by inspecting the diff; back up first if you want — e.g. `git stash` or a `.bak` copy).

| Variable | Default | Where |
|----------|---------|-------|
| `BACKEND_PORT` | 8000 | `.env` / `.env.example`, `docker-compose.yml`, [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml) (its `env` block) |
| `FRONTEND_PORT` | 3000 | `.env` / `.env.example`, `docker-compose.yml`, [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml) (its `env` block) |

> **Note**: In Kubernetes the container ports are fixed (`backend :8000`, `frontend :3000`);
> external access goes through the Ingress and per-env differences
> live in `charts/your-service-name/values.<env>.yaml`. Changing the local-development ports does not
> require rebuilding images.

---

## Script Details

### Safety Features

| Feature | Description |
|---------|-------------|
| **Dry-run** | `--dry-run` flag shows changes without modifying files |
| **Backup** | Automatic: every write is backed up to `.adapt-backup/<timestamp>/` before it happens (there is no `--backup` flag; a dry-run writes nothing, so it leaves no backup dir) |
| **Validation** | Checks that the new name is valid before applying |
| **Idempotent** | Running the same command twice produces the same result |

### Error Handling

- Scripts exit with non-zero status on failure
- Error messages indicate which file/line caused the issue
- Partial failures are reported (some files may be changed, others not)

---

## Manual Adaptation

If you need to change something the scripts don't cover:

### 1. Service Description

Edit `pyproject.toml`:

```toml
[project]
description = "Description of your actual service"
authors = [{ name = "Your Name", email = "you@your-domain.example" }]
```

### 2. Sonar Configuration

Edit `pyproject.toml`:

```toml
[tool.sonar]
projectKey = "your-actual-project-key"
projectName = "your_actual_service_name"
```

### 3. Docker Image Labels

The adapter derives the title label from the service name (`acme_orders` becomes `Acme Orders`). For another
wording, edit the `LABEL` in each `Dockerfile.*`:

```dockerfile
LABEL org.opencontainers.image.title="Your Actual Service Name"
```

### 4. CI/CD Variables

Set the repository and environment variables and secrets in GitHub (**Settings → Secrets and variables**, **Settings → Environments**); the list is in [Platform Setup](./platform-setup.md#variables-and-secrets), and `scripts/github-settings.sh` sets the variables.

---

## Verification

After adaptation, verify everything works:

```bash
# Check Python package name
python -c "import your_new_name; print(your_new_name.__version__)"

# Check backend starts (Docker Compose)
docker compose up -d backend
curl -fsS http://127.0.0.1:8000/api/v1/health

# Check frontend builds
cd frontend && pnpm run build

# Run tests
make test
cd frontend && pnpm test
```
