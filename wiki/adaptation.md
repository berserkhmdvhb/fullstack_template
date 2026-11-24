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
among others. The optional `REPOSITORY` (`owner/name`) replaces `your-org/your-repo` in the `SOURCE_URL` default of
the three Dockerfiles (the image's OCI `source` label for local and kind builds; CI passes the real URL), the README
badges, and the links in `SECURITY.md`, the issue forms and the pull request template. The optional `CODEOWNER` (a user
`@name`, a team `@org/team` or an email; GitHub rejects a bare organisation) replaces `@your-org` in
`.github/CODEOWNERS`, and the adapter refuses another shape. Unset, those placeholders stay, and the adapter prints a
notice while `CODEOWNERS` still has its placeholder. The dry-run lists every file it would change.

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

---

## Configuring Ports

There is no port-adaptation script: the retired HAProxy edge removed the single "public" port, and
the remaining ports are environment-driven. Change them by editing the relevant files directly
(dry-run by inspecting the diff; back up first if you want — e.g. `git stash` or a `.bak` copy).

| Variable | Default | Where |
|----------|---------|-------|
| `BACKEND_PORT` | 8000 | `.env` / `.env.example`, `docker-compose.yml` |
| `FRONTEND_PORT` | 3000 | `.env` / `.env.example`, `docker-compose.yml` |

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

Edit `Dockerfile.backend` and `Dockerfile.frontend`:

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
cd frontend && npm run build

# Run tests
make test
cd frontend && npm test
```
