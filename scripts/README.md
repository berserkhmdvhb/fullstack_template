# `scripts/` — local Kubernetes & infra operations toolkit

A small, cohesive set of bash tools for running the three-image stack
(**backend** / **frontend** / **nginx**) on a local cluster (minikube / kind).
Every script is config-driven, shares one runtime (`_lib.sh`), and follows the
same conventions so they compose into a predictable workflow.

> These are **local developer tools**, not production deployment automation.
> They favour clear output and safety (dry-runs, confirmations, no leaked
> secrets) over raw speed.

---

## Contents

| Script | One-liner |
| --- | --- |
| [`rebuild-and-roll.sh`](#rebuild-and-rollsh) | Build each image, verify it, roll the workload, health-check it |
| [`e2e.sh`](#e2esh) | Deploy + verify the whole stack end-to-end (the "did it actually come up?" gate) |
| [`k8s-env.sh`](#k8s-envsh) | Apply / delete / status / wait for a whole Helm environment (release) |
| [`port-forward.sh`](#port-forwardsh) | Self-cleaning port-forwards to every service |
| [`logs.sh`](#logssh) | Aggregate (or live-tail) logs across services |
| [`secrets.sh`](#secretssh) | Create / inspect / delete a namespace Secret, safely |
| [`db-backup.sh`](#db-backupsh) | Trigger the pg-backup CronJob and fetch the dump |
| [`smoke-test.sh`](#smoke-testsh) | Synthetic end-to-end HTTP checks with text/JSON/JUnit reports |
| [`certs-local.sh`](#certs-localsh) | Wire self-signed TLS for local HTTPS (cert-manager) |
| [`_lib.sh`](#shared-conventions) | Shared runtime: logging, dry-run, secret safety, namespace discovery |
| [`services.yaml`](#servicesyaml) | The service registry every loop script reads |

---

## Shared conventions

Because every script sources `_lib.sh`, they all behave the same way:

- **Framed banners + emoji, timestamped logs.** A banner prints once at the top;
  each phase is a labelled divider. Steps `▶`, info `ℹ`, success `✅`,
  warnings `⚠` and errors `❌` (the last two go to **stderr**).
- **`--dry-run` everywhere.** Prints the exact command (`⇢ …`) and executes
  nothing. If a script would touch the cluster, `--dry-run` shows you what it
  would do first. (`port-forward --list` and `secrets show` are read-only.)
- **Config-driven, not hard-coded.** Namespaces, image tags, and ingress hosts
  come from the **Helm values file** (`charts/your-service-name/values.<env>.yaml`),
  never from inside the scripts. `services.yaml` holds only structure.
- **`--env local|dev|qa|production`** on the loop scripts resolves the namespace *and*
  image tag from the chart's values file (`lib_helm_ns` + `lib_helm_values_file` in
  `_lib.sh`) — so the script and the manifests can never drift. Explicit
  `--namespace` / `--tag` still win.
- **Secret safety.** Secrets are parsed without `source` (no code execution),
  are **never printed whole** (values are masked like `su****`), and the secret
  scripts **refuse to run as root**.
- **Fail loudly.** `set -euo pipefail` + an ERR trap that names the failing line.
- **`--only key1,key2`** on the loop scripts restricts the run to a subset
  (controlled blast radius).

### Namespace resolution (same rule in every script)

The chart uses a deterministic per-env-per-tier convention (no overlay to discover it
from); `lib_helm_ns` in `_lib.sh` computes it:

```
--namespace  (explicit, wins)
   ↓
--env → <HELM_RELEASE>-<env>        (app ns, e.g. your-service-name-dev)
        <app-ns>-db                 (db ns, e.g. your-service-name-dev-db)
   ↓
services.yaml `namespace:`  (fallback)
```

---

## `services.yaml`

The single service registry. The loop scripts (`rebuild-and-roll`,
`port-forward`, `logs`) read it — **add a row to add a service; edit no script.**

```yaml
namespace: your-service-name          # global default namespace
services:
  - key: backend                      # MUST match app.kubernetes.io/component
    workload: your-service-name-backend
    port: 8000
    health_path: /api/v1/health
    probe_tool: curl                  # curl | wget | node — see below
    verify: 'test -f server.js && echo OK || { echo MISSING >&2; exit 1; }'
    # ... dockerfile, image, container, label
```

`probe_tool` matters because `curl` is not in every base image:

| tool | image | why |
| --- | --- | --- |
| `curl` | `python:3.12-slim` (backend) | curl is installed |
| `wget` | `nginx:*-alpine` | busybox `wget` |
| `node` | `node:*-slim` (frontend) | global `fetch`, no curl |

---

## The scripts

### `rebuild-and-roll.sh`
The core deploy loop. For every service in `services.yaml`: **build** the image
into the node daemon → **verify** the fix is baked in → **rollout restart** the
workload → **health-check** a fresh pod from inside. A failure on one service is
recorded and reported, not fatal to the others.

```bash
./scripts/rebuild-and-roll.sh                 # build+verify+roll+health, all services
./scripts/rebuild-and-roll.sh --env qa        # namespace + tag from values.qa.yaml
./scripts/rebuild-and-roll.sh --only backend,nginx
./scripts/rebuild-and-roll.sh --no-health --tag "$(date +%Y%m%d)"
./scripts/rebuild-and-roll.sh --no-docker-env # registry-based build flow
./scripts/rebuild-and-roll.sh --dry-run
```

`--env` reads the namespace **and** the image tag (`images[].newTag`) from the
chart's values file (`values.<env>.yaml`). Explicit `--namespace` / `--tag` still win.

### `k8s-env.sh`
Lifecycle for a whole environment — one command instead of a runbook's worth of
`kubectl`. `apply` runs `helm upgrade --install` against `charts/your-service-name`
with `values.<env>.yaml`, creates both namespaces (`--create-namespace`), lets the
`post-install` migration hook run, then waits for every service to be Ready.
`delete` uninstalls the release; `status` / `wait` inspect or block on readiness.

```bash
./scripts/k8s-env.sh apply --env dev          # helm upgrade --install → wait Ready
./scripts/k8s-env.sh apply --env dev --no-wait
./scripts/k8s-env.sh status --env production
./scripts/k8s-env.sh wait  --env dev --only backend
./scripts/k8s-env.sh delete --env dev --yes
./scripts/k8s-env.sh apply --env dev --dry-run
```

> Secrets and the migration are **owned by the chart** — `apply` injects nothing by hand.
> App + db-credentials secrets render from `values.<env>.yaml`; the migration runs as a
> `post-install,post-upgrade` hook that re-runs on every upgrade and auto-deletes on success.

### `e2e.sh`
The **"did the whole thing actually come up correctly?"** gate — one command that
stands up the environment *and* proves it is healthy in-cluster, instead of pasting
a dozen `kubectl` diagnostics. It answers what `k8s-env.sh apply` (only waits for
Ready) and `smoke-test.sh` (only checks the HTTP surface) leave open:

- Is Postgres actually **Running** (not crash-looping)?
- Did the migration **complete** (release `deployed` — the post-install hook succeeded)?
- Is the backend on **real Postgres**, not the SQLite fallback?
- Are replicas / HPA / ResourceQuota consistent (no stuck rollout)?
- Do the live objects still match what the **chart** renders?

Each check prints its raw diagnostics and a **PASS / WARN / FAIL** verdict; the
command exits non-zero if anything FAILs, so it doubles as a CI gate.

```bash
./scripts/e2e.sh                        # full local-dev deploy + verify (default: all)
./scripts/e2e.sh verify --env dev       # read-only in-cluster sweep, no apply
./scripts/e2e.sh render --env dev       # render the chart, assert the invariants
./scripts/e2e.sh deploy --env dev       # secrets → apply → verify
./scripts/e2e.sh deploy --env dev --dry-run
```

**What `deploy` does, step by step** (all read from `values.<env>.yaml` — nothing hard-coded):

1. Delegates the apply to `k8s-env.sh apply --env <env>` (`helm upgrade --install` against the chart).
   The chart creates **both** namespaces (app + `<app>-db`); nothing is pre-created.
2. **Secrets render from the values file** — the app Secret and the `…-db-credentials` secret come from
   `values.<env>.yaml` (dev/local defaults match the bundled Postgres init creds; `qa`/`production` leave
   them empty for a managed DB + sealed/ESO/SOPS). Nothing is injected by hand, nothing committed.
3. The migration runs as a `post-install,post-upgrade` hook (re-runs every upgrade, auto-deletes on success).
4. Runs the `verify` sweep and prints a verdict summary.

`render` is client-side (no cluster needed) — it asserts the **rendered chart** contains the StatefulSet,
runs Postgres as non-root (UID 999), ships a `postgresql+asyncpg` `DATABASE_URL`, and renders with no
template errors. `--dry-run` prints the mutating commands and skips the live-cluster `verify`.

Local-dev secret defaults (override with env vars; they match the bundled dev
PostgreSQL init creds and what `values.<env>.yaml` supplies):

| Var | Default | Secret / key |
| --- | --- | --- |
| `DEV_DB_USER` | `app` | db-credentials / `username` |
| `DEV_DB_PASSWORD` | `devpassword` | db-credentials / `password` |
| `DEV_DB_NAME` | `service_template` | db-credentials / `database` |
| `DEV_DB_HOST` / `DEV_DB_PORT` | `your-service-name-postgresql` / `5432` | backend/migration `DATABASE_URL` host |

### `port-forward.sh`
Opens `kubectl port-forward` tunnels to every service and **kills them all on
exit / Ctrl-C** — no orphaned forwards holding a local port. Waits for each
tunnel to accept connections before returning (so a follow-on script doesn't
race the pod).

```bash
./scripts/port-forward.sh --env dev           # all services
./scripts/port-forward.sh --only backend      # just the backend
./scripts/port-forward.sh --env qa --port backend=9000
./scripts/port-forward.sh --list --env dev    # preview, start nothing
```

### `logs.sh`
Aggregate the last N lines of every service (each line prefixed with the service
key), or `-f` to live-tail them all concurrently in one stream.

```bash
./scripts/logs.sh --env dev                   # last 100 lines, all services
./scripts/logs.sh --env dev --only backend --tail 300
./scripts/logs.sh -f --env dev --since 5m     # live, interleaved; Ctrl-C stops all
```

### `secrets.sh`
Create-or-update a `generic` Secret from a `.env` or `--key VALUE` pairs, or
inspect/delete one. Values are masked everywhere; `show` lists only key names.

```bash
./scripts/secrets.sh update --env dev -f .env
./scripts/secrets.sh update --env qa --key DB_PASSWORD=hunter2 --key DB_USER=app
./scripts/secrets.sh show --env dev                       # keys only, values masked
./scripts/secrets.sh update --env dev -f .env --required DB_PASSWORD,DB_USER --dry-run
./scripts/secrets.sh delete --env dev --yes
```

### `db-backup.sh`
Takes an on-demand PostgreSQL backup the way the scheduled CronJob does, then
copies the dump off the backup PVC. Since `kubectl cp` can't reach a *completed*
pod, it attaches a short-lived "fetch" pod to the same PVC, copies the file, and
throws the pod away.

```bash
./scripts/db-backup.sh --env dev
./scripts/db-backup.sh --env dev --no-cleanup   # keep the Job + fetch pod to debug
./scripts/db-backup.sh --env dev --dry-run
```

### `smoke-test.sh`
Exercises the live HTTP surface like a real client and reports pass/fail per
check — the "did it actually deploy and is it serving?" gate. Emits text, JSON,
or JUnit (for CI). Exit code is non-zero on any failure.

```bash
./scripts/port-forward.sh --only backend &            # in one terminal
./scripts/smoke-test.sh --base-url http://localhost:8000
./scripts/smoke-test.sh --format junit --out smoke.xml   # CI test report
# a release whose backend checks the Host header (dev, qa, production; the local values allow
# localhost): send the backend Service name as Host, as the frontend does in-cluster
./scripts/smoke-test.sh --base-url http://localhost:8000 --host <release>-backend
```

Checks: `GET /health`, `GET /healthz`, `POST /echo` round-trip,
`GET /echo/history`, `GET /items`, and an `items` create → read → delete cycle.

### `certs-local.sh`
Gives the local stack real HTTPS using cert-manager's bundled `selfsigned`
ClusterIssuer. Applies the issuer, reads the **chart's `values.<env>.yaml`** Ingress
TLS `secretName` + hosts, creates a matching Certificate, and waits for the secret.

> **LOCAL-DEV ONLY.** Never run this against production — production uses
> `letsencrypt-prod`. After setup, map the hosts to `127.0.0.1` and trust the CA
> (`mkcert -install && mkcert api.local app.local 127.0.0.1`).

```bash
./scripts/certs-local.sh --env dev
./scripts/certs-local.sh --env dev --no-ca      # issuer already applied
```

---

## The two-namespace scheme (per-env-per-tier)

The stack uses **two** namespaces per environment: an app namespace (stateless
workloads) and a `…-db` namespace (the database), so RBAC / NetworkPolicy / backup
policy can differ between them. The **Helm chart** creates both — no Kustomize
`namespace:` field, no hand-patched overlays.

The convention is deterministic (`lib_helm_ns` in `_lib.sh`), derived from the
release name (`your-service-name`) and the environment:

- app ns = `<release>-<env>` — e.g. `your-service-name-dev`
- db  ns = `<app-ns>-db`     — e.g. `your-service-name-dev-db`

Both carry a stable `tier` label (`tier: app-ns` / `tier: db`) that the
NetworkPolicies select on, so the policies need no per-env editing. Result:
`--env dev` puts the app in `your-service-name-dev` and the database in
`your-service-name-dev-db`; production uses the bare `your-service-name` app ns with
an external DB. Switching env on one cluster is idempotent because the selectors
carry only stable identity (`app.kubernetes.io/name` + `instance`), never
`environment`.

> **The `k8s/` Kustomize tree was removed in Phase 2 Stage C** — the operational
> surface is fully Helm-driven and no longer reads it. The tree survives in git
> history (last touched `f72fdd1`) if the manifests ever need to be recovered.

---

## Typical workflow

```bash
# 1. Stand up the whole environment AND verify it came up correctly
#    (secrets → apply → wait Ready → Postgres/migration/backend checks)
./scripts/e2e.sh --env dev

# 2. Rebuild after a code change, roll, and health-check
./scripts/rebuild-and-roll.sh --env dev --only backend

# 3. Re-verify the in-cluster state (read-only; no apply)
./scripts/e2e.sh verify --env dev

# 4. Watch the stack and reach it locally
./scripts/logs.sh -f --env dev &
./scripts/port-forward.sh --env dev &

# 5. Confirm it actually serves HTTP (the client-surface gate)
./scripts/smoke-test.sh --base-url http://localhost:8000

# 6. Take a manual DB backup if you need one
./scripts/db-backup.sh --env dev
```

> `e2e.sh` is the outer gate: it wraps `k8s-env.sh apply` and then proves the
> deployment is correct (Postgres Running, migration completed, backend on real
> Postgres). `smoke-test.sh` is the inner gate: it exercises the live HTTP API
> once you've port-forwarded. Run `e2e.sh` first, then `smoke-test.sh`.

---

## Conventions for contributors

When you add a script here, match the house style:

1. `source …/_lib.sh`; `set -Eeuo pipefail`; an ERR trap naming the script.
2. Log through `log_*`, frame phases with `lib_banner` / `lib_sep`.
3. Support `--dry-run` (route mutating commands through `run` / `run_sh`).
4. Resolve the namespace via the standard precedence; never hard-code it.
5. Never print a secret whole; refuse root for anything that handles secrets.
6. Read structure from `services.yaml`; resolve env-specific namespace/tag from the
   chart's values file via `_lib.sh`; keep values out of code.
