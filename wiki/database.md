# Database

PostgreSQL setup, SQLAlchemy models, Alembic migrations, soft-delete pattern, and Kubernetes database deployment.

---

## Table of Contents

- [Overview](#overview)
- [Database Configuration](#database-configuration)
- [SQLAlchemy Models](#sqlalchemy-models)
- [Engine & Session](#engine--session)
- [Alembic Migrations](#alembic-migrations)
- [Database operations](#database-operations)
- [Soft-Delete Pattern](#soft-delete-pattern)
- [Repository Pattern](#repository-pattern)
- [Kubernetes Database Deployment](#kubernetes-database-deployment)

---

## Overview

The backend uses **PostgreSQL** as the primary database with a **SQLite fallback** for development and testing.

```mermaid
flowchart TB
    Env["DATABASE_URL env var"] --> Resolve{"Set?"}
    Resolve -->|"Yes"| PG["PostgreSQL<br/>via asyncpg"]
    Resolve -->|"No"| SQLite["SQLite<br/>via aiosqlite"]
    PG --> Engine["AsyncEngine"]
    SQLite --> Engine
    Engine --> Session["AsyncSession"]
    Session --> Models["SQLAlchemy Models"]
    Models --> Migrations["Alembic Migrations"]
```

---

## Database Configuration

### Connection URL

```bash
# PostgreSQL (production)
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/dbname

# SQLite (development fallback)
# Used automatically when DATABASE_URL is not set
```

### Docker Compose

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: ${DB_USER:-app}
      POSTGRES_PASSWORD: ${DB_PASSWORD:-changeme}
      POSTGRES_DB: ${DB_NAME:-service_template}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${DB_USER:-app} -d ${DB_NAME:-service_template}"]
```

---

## SQLAlchemy Models

### Item Model

The model is `Item` in [`models.py`](../src/your_service_name/backend/db/models.py) (SQLAlchemy 2.0
`Mapped` columns); the table it maps is created by the first migration,
[`0001_create_items.py`](../src/your_service_name/backend/db/migrations/versions/0001_create_items.py).

### Column Reference

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `id` | `Uuid` | No | UUID v4 | Primary key (native `UUID` on PostgreSQL, `CHAR(36)` on SQLite) |
| `name` | `String(255)` | No | — | Item name (1-255 chars) |
| `description` | `String` | No | `""` | Optional in the API; stored as an empty string |
| `is_active` | `Boolean` | No | `True` | Active status |
| `created_at` | `UTCDateTime` | No | `now()` | Auto-set on create |
| `updated_at` | `UTCDateTime` | No | `now()` | Auto-updated on change |
| `deleted_at` | `UTCDateTime` | Yes | — | Soft-delete timestamp |

`UTCDateTime` is `DateTime(timezone=True)` on PostgreSQL (no schema change). On SQLite, which stores no
zone, it attaches UTC to loaded values; it converts an aware value to UTC on write and **refuses a naive
one**, so every timestamp the API returns carries an explicit offset.

---

## Engine & Session

### Engine Creation

```python
# src/your_service_name/backend/db/engine.py

def _resolve_database_url(settings: Settings) -> str:
    """TEST_DATABASE_URL > DATABASE_URL > SQLite fallback (settings.SQLITE_PATH)"""
    url = settings.database_url
    if url:
        return url
    return "sqlite+aiosqlite:///./data/items.db"

def create_engine(settings: Settings) -> AsyncEngine:
    url = _resolve_database_url(settings)
    if url.startswith("sqlite"):
        return create_async_engine(url, connect_args={"check_same_thread": False})
    return create_async_engine(url, pool_pre_ping=True)
```

### Session Management

```python
@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a session, commit on success, rollback on error, close in finally."""
    session = async_session_maker()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
```

---

## Alembic Migrations

### Configuration

```ini
# alembic.ini

[alembic]
script_location = src/your_service_name/backend/db/migrations
sqlalchemy.url = postgresql+asyncpg://...

[post_write_hooks]
hooks = ruff
ruff.type = exec
ruff.command = ruff format -- STDIN
```

### Creating Migrations

```bash
# Auto-generate migration from model changes
alembic revision --autogenerate -m "add items table"

# Create empty migration
alembic revision -m "add index on items.name"
```

### Running Migrations

```bash
# Apply all pending migrations
alembic upgrade head

# Rollback one migration
alembic downgrade -1

# Show current revision
alembic current

# Show pending migrations
alembic history
```

### Migration Runner

`python -m your_service_name.backend.db.run_migrations` is the one way the app's database is migrated
(`alembic upgrade head` above is the developer's tool). It waits for the database (bounded retry,
never logging the URL), then acts on the database's revision:

| State | Action |
|-------|--------|
| at the code's head | no-op |
| behind, or empty | `upgrade head` |
| at a revision this release does not know (newer) | logs "database is ahead of this release; nothing to do" and exits 0; **never downgrades** |
| more than one head in the scripts | fails |

The app does **not** migrate at startup. Each entry point migrates first: the chart's Job, the compose
`migrate` service (the backend waits for it), `./run_backend.sh`, `infra/runtime/entrypoint.sh` and the
CI job that boots Uvicorn.
The image's own launcher (`python -m your_service_name.backend.serve`) only serves; it never migrates.

---

## Database operations

### The migration Job

The chart runs the runner as a Job named `<fullname>-migrate-r<revision>`: one per release revision (a Job's
spec is immutable), a plain release resource with `ttlSecondsAfterFinished: 86400`, **not a Helm hook**.

Why not a hook: readiness checks the schema (below), and Helm waits for the release's resources to be
ready *before* it runs post-install and post-upgrade hooks (Helm `pkg/action`: `install.go` and
`upgrade.go` run the wait, then the post hooks). A post-install migration would wait for Pods that
wait for the schema it creates, which deadlocks every first install. As a normal resource the Job starts
with the Deployment; the new Pods stay unready until it finishes and a rolling update keeps the old Pods
serving meanwhile. `scripts/ci/deploy-env.sh` passes `--wait-for-jobs`, so a failed migration fails the
release and the existing rollback runs. Operating it: [operations runbook](./operations-runbook.md#database-migrations).

Every other intent of a hook design holds: the app never migrates at startup, the schema leads the code
(new Pods stay unready until it matches), and a failed migration fails the release. The name carries `.Release.Revision`, which `helm template` always renders as `1`: the supported path is
`helm upgrade`, and a tool that applies rendered manifests would find the previous Job's immutable spec in its way.

**The Job's database settings.** The Job never reads the app ConfigMap. Its init and migrate containers get an
explicit env list: `DB_HOST`, `DB_PORT`, `DB_NAME` and, when `config` sets them, `ENV`, `DB_SSLMODE`,
`DB_CONNECT_TIMEOUT_SECONDS` and `DB_MIGRATION_LOCK_TIMEOUT_MS`, rendered from the same values and helpers as
the ConfigMap (so they cannot drift), plus `DB_SSLROOTCERT` when `database.tls.caSecret` is set, and `DB_USER`
and `DB_PASSWORD` from the app Secret, whichever of the three [secret sources](./security.md#secret-sources)
holds it. These are the settings the runner reads (`db/run_migrations.py`, `db/connect.py`). The Job does not get
the app's other settings, so the runner (`Settings.for_migration`) loads `Settings` with `ENV` set to development and,
when the real `ENV` is production, runs only the database checks (`Settings.database_problems`): SQLite and a missing
URL are refused, and so are `DB_SSLMODE` `disable` or `require` to a remote host, **before any connection**. A
loopback host with `disable` (the Auth Proxy) passes, as in the app.

### Proven on kind

`tests/k8s/test_migrations.py` runs the whole lifecycle on the local kind cluster, on a release and namespaces of its own
(created by the test and removed at the end), deployed as the bootstrap's deployer the way `scripts/kind-deploy.sh`
deploys (`--wait --wait-for-jobs`). After `make kind-up kind-deploy`, run it with `make kind-migration-test` (it stops
at the first failure; about 15 minutes); CI runs it on the kind job's floor leg
([Local cluster (kind)](./infrastructure.md#local-cluster-kind)). In order, it proves:

| Case | What it shows |
|------|---------------|
| First install, in-cluster PostgreSQL | the Job completes in one attempt, no container restarts anywhere, the Job is in the release manifest and not among `helm get hooks` (only `helm test`'s hooks are), the schema is at head |
| No-op upgrade (a config change rolls the backend) | a new revision's Job runs and finds the database at head; the backend Service's ready endpoints, polled every 0.5 s, are never empty |
| Rollback within the TTL | the rollback renders the older revision's kept Job unchanged; Helm takes it over without re-running it (same uid, one Pod), and the newer Job stays (resource-policy keep) |
| Rollback after the TTL | the TTL controller removed the Job (its mutable TTL lowered to 1 s); with the database stamped with a revision the code does not know, the rollback recreates the Job, which logs "database is ahead of this release; nothing to do" and exits 0; the Pods count that as ready |
| Failed upgrade | the migration fails on purpose, Helm (4) fails the upgrade as soon as the Job is Failed, the running Pods keep serving (endpoints never empty), and the deploy path's report prints the failed Job's logs |
| A migration over 150 s | with the database emptied (the `public` schema dropped and recreated as a new database has it, checked against one), a first migration that sleeps 160 s first: the backend stays unready the whole time, becomes ready after the Job, and nothing restarts (the startup and liveness probes do not check the schema) |
| Timeouts | through the backend's own settings: `DB_STATEMENT_TIMEOUT_MS` cancels a slow query, `DB_CONNECT_TIMEOUT_SECONDS` ends a connect the network drops, `DB_MIGRATION_LOCK_TIMEOUT_MS` ends a wait on a locked table; the Job gets the same values |
| TLS modes | against the shipped PostgreSQL (no TLS) `disable` connects in plaintext and every other mode refuses rather than fall back; with TLS turned on there by the test (a test CA): `require` encrypts, `verify-ca` accepts a name not in the certificate, `verify-full` refuses it, a wrong CA or the system trust store is refused, and the chart's path (`verify-full` with `database.tls.caSecret`) migrates and serves over TLS |
| Autogenerate | `alembic check` against the migrated database finds no change: the models and the migrations agree |
| Failed first install, then a retry | deployed by `scripts/ci/deploy-env.sh` itself (below), on a wiped release: the migration fails, `--rollback-on-failure` uninstalls the release and keeps the Failed Job, whose logs the deploy prints; the same deploy without the failure deletes that Job itself (no deployed revision: `migration_job_purge_failed`) and installs revision 1, whose Job finds the database untouched; nothing is removed by hand |

**A failed first install ends only at Helm's `--timeout`.** The Job is Failed within about two minutes, but
the backend cannot become ready without the schema, so Helm keeps waiting for it (a failed upgrade, whose running
Pods stay ready, fails as soon as the Job does). `scripts/ci/deploy-env.sh` then uninstalls the release
(`--rollback-on-failure`), so its retry starts fresh. A deploy without `--rollback-on-failure` leaves the failed release
and its Deployment in place: it must keep `--timeout` below the backend's
[`rollout.progressDeadlineSeconds`](../charts/your-service-name/values.yaml), or the Deployment is marked
`ProgressDeadlineExceeded` before the retry and Helm fails the retry on it at once (although the Pod becomes ready
seconds after the retry's Job). `scripts/kind-deploy.sh` and `scripts/migrate-to-bootstrap.sh` are such deploys, so
their `--timeout` stays below it; [`test_deploy_timeouts.py`](../tests/supply_chain/test_deploy_timeouts.py) holds every
deploy path's wait to that rule ([Deploy chain](./ci-cd.md#deploy-chain)).

The failed-first-install case runs `deploy-env.sh` itself, through its test-only `DEPLOY_ENV=kind`
([the deploy script on kind](./ci-cd.md#proven-on-kind)), so its namespaces are `<release>-kind` and `<release>-kind-db`:
the kind job's images pushed to a throwaway registry for their digests, the deployer, the deploy workflow's default
`HELM_TIMEOUT`, and this release's timeouts as a values file. A passing retry also runs the script's verify and smoke.

The slow and failed migrations come from a test-only hook: `tests.migration.delaySeconds` and `tests.migration.fail`
(off by default) give the Job `MIGRATION_TEST_DELAY_SECONDS` and `MIGRATION_TEST_FAIL`, only with `tests.enabled`,
which a production render refuses; the runner refuses both in production too. What kind cannot show: a managed
database's own CA and certificate rotation, and a migration that really changes the schema across two image versions
(the base and head images are the same build here, so a failed upgrade's new Pods would be ready).

### Expand/contract policy

A migration must stay compatible with the **previous release's code**: add before you use (a new nullable
column or table first, the code that uses it in the next release) and remove one release after the code
stopped using it. This is what makes two behaviours safe: a database *ahead* of the code counts as
ready (a newer release migrated first), and `helm rollback` is safe: within the Job TTL the older revision's kept Job already exists and is updated in
place without re-running; after it the Job is recreated, finds the database ahead and exits 0. Do not rename or drop a column in the same release that stops using it.

### Readiness and liveness

`GET /api/v1/health` (the **readiness** probe) is read-only and answers:

| Outcome | Status | Body |
|---------|--------|------|
| database reachable, revision is the code's head, or unknown to the code (ahead) | 200 | `{"status": "ok"}` |
| database unreachable (or the engine failed to start) | 503 | `{"status": "degraded", "reason": "database_unreachable"}` |
| `alembic_version` missing or empty | 503 | `{"status": "degraded", "reason": "schema_missing"}` |
| revision is a known older one | 503 | `{"status": "degraded", "reason": "schema_behind"}` |

The code's expected head is read once at startup from the bundled scripts. The body never names a revision,
URL or error. `GET /api/v1/healthz` is process-only: no database, no schema check. The chart uses it for the **liveness
and startup** probes, so a Pod is never restarted while it waits for a migration (the Job may outlast any
startup budget); only readiness gates on the schema.

### Timeouts and pool sizing

All are [settings](./environment-variables.md#backend-settings), applied to PostgreSQL only (SQLite ignores them):
`DB_CONNECT_TIMEOUT_SECONDS` (asyncpg `timeout`), and, for the app engine only, `DB_STATEMENT_TIMEOUT_MS` and
`DB_IDLE_IN_TRANSACTION_TIMEOUT_MS` (asyncpg `server_settings`). The migration run sets
`lock_timeout` from `DB_MIGRATION_LOCK_TIMEOUT_MS`, so a migration waiting on a busy table fails
instead of blocking traffic, and **no** `statement_timeout`, because a long migration is legitimate.

The pool is **per process**: connections opened = (`DB_POOL_SIZE` + `DB_MAX_OVERFLOW`) x replicas (x the
HPA maximum), and that total plus the migration Job must stay below the server's `max_connections`.
Size the pool from that budget, not from one Pod.

### TLS to the database

`DB_SSLMODE` and `DB_SSLROOTCERT` build an `ssl.SSLContext` (TLS 1.2 or later):

| `DB_SSLMODE` | Behaviour |
|--------------|-----------|
| `disable` (default) | no TLS (passed explicitly: asyncpg would otherwise "prefer" TLS) |
| `require` | encrypts, verifies nothing |
| `verify-ca` | verifies the certificate chain, not the hostname |
| `verify-full` | verifies the chain and the hostname |

The verify modes trust the file in `DB_SSLROOTCERT` alone when it is set, and the image's system trust
store otherwise. `ssl` or `sslmode` in the `DATABASE_URL` query is refused (it would conflict with
`DB_SSLMODE`). **Production refuses `disable` and `require` unless every host the URL names is loopback or a
unix socket**: a URL with no host at all (or only `PGHOST` to fall back on) is refused too, because
Settings cannot see what would be used. The chart's production values set `verify-full`.

**A managed database's CA.** Its CA is usually not in the image's trust store and the chart's root
filesystem is read-only. Name an existing Secret holding the CA certificate:

```yaml
database:
  tls:
    caSecret: {name: <ca-secret>, key: <key-in-the-secret>}
```

The chart mounts it read-only at `/etc/db-ca/ca.pem` in the backend Deployment **and** the migration Job and
sets `DB_SSLROOTCERT` to that path. It is empty in the production values on purpose: a missing CA fails
loudly at the first connection.

**Cloud SQL, direct connection.** With a per-instance server CA, verifying the CA verifies the server's
identity, and the certificate does not carry a private IP, so use `verify-ca` with the instance CA in
`database.tls.caSecret` (`verify-full` fits a shared CA or a DNS name in the certificate); see Google's
[SSL/TLS certificates page](https://cloud.google.com/sql/docs/postgres/configure-ssl-instance).

**Cloud SQL with the Auth Proxy** (a chart switch; placeholders). The proxy encrypts the hop itself, so the
database host is `127.0.0.1` and `config.DB_SSLMODE` is `disable` (a loopback host is allowed in production).
The proxy must be running before the migration's `wait-for-db` init container checks the database, and it must
not stop the Job from completing, so it runs as a **native sidecar**: an init container with
`restartPolicy: Always` (on by default since Kubernetes 1.29, stable in 1.33; see the Kubernetes
[sidecar containers](https://kubernetes.io/docs/concepts/workloads/pods/sidecar-containers/) page, which also
covers Jobs). Turn it on and the chart adds it to the backend Deployment and the migration Job, ahead of
`wait-for-db`, sets `DB_HOST` to `127.0.0.1` (the ConfigMap and the Job alike) and allows the egress:

```yaml
postgresql: {enabled: false}
config: {DB_SSLMODE: disable}
cloudSqlProxy:
  enabled: true
  instanceConnectionName: <project>:<region>:<instance>
  privateIp: true               # --private-ip
  autoIamAuthn: false           # --auto-iam-authn
  egressCIDRs: [<instance-private-range>, 199.36.153.8/30]
```

The proxy listens on `config.DB_PORT`, logs as structured JSON, and runs restricted (non-root, read-only root
filesystem, all capabilities dropped, `RuntimeDefault` seccomp) with `cloudSqlProxy.resources`. The image is
`cloudSqlProxy.image` (repository, tag, digest), pinned by its multi-arch index digest; Renovate refreshes it. The
NetworkPolicy is IP-based, so `cloudSqlProxy.egressCIDRs` is required: the backend and the Job may reach those
ranges on 3307 (the instance) and 443 (Google APIs). With a private-IP instance list its range; for Google APIs
over Private Google Access add `199.36.153.8/30` (`private.googleapis.com`) or the VIP range your VPC uses.
`externalDatabase.egressCIDR` is not required with the proxy. The Pod's service account needs the Cloud SQL
Client role through Workload Identity.

- **Metadata server egress.** The proxy gets its token from the GKE metadata server, which the default-deny
  egress also blocks. `cloudSqlProxy.metadataEgress` (a list of `{cidr, ports}`) allows it on the backend and
  the Job. By default it holds both GKE forms: `169.254.169.252/32` on 987 and 988 (Workload Identity on GKE 1.21
  and later with the standard dataplane) and `169.254.169.254/32` on 80 (GKE Dataplane V2). Trim it to your
  dataplane; an empty list renders no rule. `networkPolicy.migrationExtraEgress` appends any other egress rule
  to the Job, as `backend.networkPolicy.extraEgress` does for the backend.
- **Render checks.** The proxy needs `postgresql.enabled=false` (the host is `127.0.0.1`, the in-cluster
  database would be unused) and `config.DB_SSLMODE` unset or `disable`: the proxy encrypts to Cloud SQL, the hop
  to loopback is local, and a verifying mode fails against the plaintext listener (production's `verify-full`
  must be overridden). The backend's production check accepts `disable` because its `DATABASE_URL` names a
  loopback host (`_is_loopback_database`); the Job's runner applies the same check.
- **Startup.** The sidecar runs with `--health-check`, `--http-port` (`cloudSqlProxy.healthPort`) and
  `--http-address=0.0.0.0` (the kubelet probes the Pod IP) and has a startup probe on `/startup`, so the main
  container starts after the proxy is up. The address exposes only the health endpoints on the Pod IP: no metrics
  (`--prometheus`), quit (`--quitquitquit`) or debug endpoint is enabled, the database listener stays on
  `127.0.0.1`, and the default-deny policy keeps other Pods out, so only the kubelet probes it.
- **IAM authentication.** Set `autoIamAuthn`, grant the Cloud SQL Instance User role, and use the database
  user `<service-account>@<project>.iam`. The proxy supplies the password, so `DB_PASSWORD` may be missing from
  the app Secret (the Job reads it as an optional key; drop it from `secrets.externalSecret.remoteKeys`). An absent
  key leaves the literal `$(DB_PASSWORD)` in the URL, which the proxy ignores.
- Your own sidecars still go in `backend.extraInitContainers`, ahead of the proxy. `guards.rejectPlaceholders`
  refuses the placeholder instance connection name.

**Cloud SQL over Private Service Connect.** No proxy: the instance gets a PSC endpoint, a private IP in your
VPC. Set `externalDatabase.host` to that endpoint's address or DNS name, `externalDatabase.egressCIDR` to the
endpoint's subnet (the :5432 egress rule renders from it), `postgresql.enabled` to `false` and `DB_SSLMODE` to
`verify-ca` or `verify-full` with the instance CA in `database.tls.caSecret` (the server certificate does not
carry the endpoint's address, so `verify-ca` fits the per-instance CA). The chart renders nothing PSC-specific.
See Google's [Private Service Connect page](https://cloud.google.com/sql/docs/postgres/configure-private-service-connect).

---

## Soft-Delete Pattern

Items are **never hard-deleted**. Instead, the `deleted_at` column is set:

```mermaid
flowchart LR
    Delete["DELETE /items/{id}"] --> Set["SET deleted_at = now()"]
    Set --> Hide["Hidden from list queries"]
    Hide --> Archive["Still in database"]
```

### Query Filtering

All list queries automatically exclude soft-deleted items:

```python
# Repository: list_items
stmt = select(Item).where(Item.deleted_at.is_(None))
```

### Restore

To "undelete" an item, set `deleted_at` back to `None`:

```python
item.deleted_at = None
await session.commit()
```

---

## Repository Pattern

### Protocol (Interface)

```python
# src/your_service_name/backend/repositories/item_protocol.py

class ItemRepository(Protocol):
    async def list_items(
        self,
        search: str | None = None,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[ItemEntity], int]: ...

    async def get_item(self, item_id: uuid.UUID) -> ItemEntity | None: ...
    async def create_item(self, name: str, description: str, *, is_active: bool) -> ItemEntity: ...
    async def update_item(self, item_id: uuid.UUID, **fields: str | bool | None) -> ItemEntity | None: ...
    async def delete_item(self, item_id: uuid.UUID) -> bool: ...
```

### Implementation

```python
# src/your_service_name/backend/repositories/sqlalchemy_item.py

class SqlAlchemyItemRepository(ItemRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_items(self, search=None, *, offset=0, limit=50) -> tuple[list[ItemEntity], int]:
        # Base query with the optional search filter (soft-deleted rows excluded)
        base_stmt = select(Item).where(Item.deleted_at.is_(None))
        if search:
            pattern = f"%{search}%"
            base_stmt = base_stmt.where(Item.name.ilike(pattern) | Item.description.ilike(pattern))
        # ... the total count, then offset and limit, newest first
```

### In-Memory Repository (Testing)

```python
# src/your_service_name/backend/repositories/in_memory_echo.py

class InMemoryEchoRepository:
    """In-memory repository for testing without a database."""
    def __init__(self):
        self._records: list[EchoRecord] = []
```

---

## Kubernetes Database Deployment

### Option 1: In-Cluster StatefulSet (Dev/Testing)

For development and testing, use the included PostgreSQL StatefulSet:

```yaml
# now in the Helm chart: charts/your-service-name/templates/postgresql-statefulset.yaml (in-cluster DB, dev/testing only; k8s/ tree removed)
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: your-service-name-postgresql
  namespace: your-service-name-db
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: your-service-name-postgresql
  template:
    spec:
      containers:
        - name: postgresql
          image: postgres:16-alpine
          env:
            - name: POSTGRES_USER
              valueFrom:
                secretKeyRef:
                  name: your-service-name-db-credentials
                  key: username
            - name: POSTGRES_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: your-service-name-db-credentials
                  key: password
            - name: POSTGRES_DB
              valueFrom:
                secretKeyRef:
                  name: your-service-name-db-credentials
                  key: database
          volumeMounts:
            - name: postgresql-storage
              mountPath: /var/lib/postgresql/data
  volumeClaimTemplates:
    - metadata:
        name: postgresql-storage
      spec:
        accessModes: ["ReadWriteOnce"]
        resources:
          requests:
            storage: 10Gi
```

**Pros**: Simple, self-contained, no external dependencies  
**Cons**: Not suitable for production, no high availability, manual backup required

### Option 2: External Managed Database (Production)

For production, use a managed PostgreSQL service (AWS RDS, GCP Cloud SQL, Azure Database):

```yaml
# now in the Helm chart: charts/your-service-name/templates/database-external.yaml (Secret template, production managed DB)
apiVersion: v1
kind: Secret
metadata:
  name: your-service-name-db-external
  namespace: your-service-name-db
type: Opaque
stringData:
  host: "your-rds-endpoint.rds.amazonaws.com"
  port: "5432"
  username: "app"
  password: "REPLACE_WITH_SEALED_SECRET"
  database: "service_template"
```

Point the chart at the managed database via `values.production.yaml` — the chart renders `DB_HOST`
(and the `:5432` egress allow when `externalDatabase.egressCIDR` is set) from these values; the app
assembles `DATABASE_URL` from `DB_HOST`/`DB_USER`/`DB_PASSWORD`/`DB_NAME` at runtime:

```yaml
# charts/your-service-name/values.production.yaml
postgresql:
  enabled: false                 # use the managed DB, not the in-cluster StatefulSet
externalDatabase:
  enabled: true
  host: "<external-host>"          # the port is config.DB_PORT (the one source)
  name: service_template
  user: app
  # password comes from an out-of-band secret (sealed-secrets / ESO / SOPS)
```

**Pros**: High availability, automated backups, managed scaling, security patches  
**Cons**: External dependency, cost, network latency

### Database Migration Job

Migrations run as a per-revision Job that is a plain release resource, not a Helm hook; see
[The migration Job](#the-migration-job). Its template is
`charts/your-service-name/templates/migration-job.yaml`.

### Database Backup CronJob

Automated daily backups via CronJob (`postgresql.backup.enabled`, on by default with the in-cluster
database), and a weekly verify CronJob that restores the newest dump into a scratch database. How
the chart keeps them deployable on any storage class:
- **The backup PVC binds at install.** The PostgreSQL Pod mounts it read-only at `/backup`, so it has a
  consumer from the first install: a `WaitForFirstConsumer` class (kind's `standard`, GKE's
  `standard-rwo`, EKS's gp2 and gp3) binds it when PostgreSQL schedules, and `helm --wait` (the
  deploy's) never waits on a `Pending` PVC. PostgreSQL never writes to it.
- **The jobs run on the database's node.** The volume is ReadWriteOnce, so both CronJobs carry a
  required `podAffinity` to the PostgreSQL Pod (`kubernetes.io/hostname`).
- **The storage class:** `postgresql.backup.storage.className` (and `postgresql.storage.className`)
  default to `standard`; `""` leaves `storageClassName` out, so the cluster's default class is used
  (on a cluster with no class named `standard`). Both fields are immutable on an existing PVC.
- **The NetworkPolicy** admits this release's backup and verify jobs to PostgreSQL, in its own
  namespace.
- **One group for the volume.** PostgreSQL (uid 999), the backup and the verify jobs (uid 10001) all
  run with `fsGroup: 999` and `fsGroupChangePolicy: OnRootMismatch`, so the kubelet gives the volume
  one group and never flips it between them, and the jobs write it through that group. The data
  volume is mounted by PostgreSQL alone and keeps its ownership.
- **PostgreSQL follows the backup volume's placement.** Because it mounts the backup PVC, the
  PostgreSQL Pod can only run where that volume can attach. Two cases leave the database itself
  `Pending` (`kubectl -n <ns>-db describe pod <fullname>-postgresql-0` shows a volume node affinity
  conflict):
  - an existing backup PV already bound on another node or zone (an older release whose nightly
    job ran elsewhere, on local or zonal disks). Remedy: copy the dumps off it, delete the backup PVC
    (`kubectl -n <ns>-db delete pvc <fullname>-pg-backup-pvc`) and deploy again: the new one binds
    beside the database;
  - an `Immediate` class on a multi-zone cluster, which may put the data and backup volumes in
    different zones. Remedy: give `postgresql.backup.storage.className` a `WaitForFirstConsumer`
    class (both then bind in the database Pod's zone), before the first deploy.
- The backup shares the database's node and, on a node-local class, its disk: copy dumps off the
  cluster for a real off-site backup.
- **A deadline bounds each run.** `postgresql.backup.deadlineSeconds` is the backup Job's
  `activeDeadlineSeconds` (the default is in `values.yaml`): a hung `pg_dump` fails the Job instead of
  blocking it. It bounds the whole run (the dump, gzip and the write to the volume), so raise it for a
  database whose dump takes longer; otherwise the backup fails every night
  ([BackupJobFailed](./operations-runbook.md#backupjobfailed)). `postgresql.backup.verifyDeadlineSeconds`
  bounds the restore check the same way.

Restoring a dump, the recovery targets and the drills: [Disaster Recovery](./disaster-recovery.md).

**What is proven where.** The binding at install, the dump and its restore run on every pull request
in the kind job. kind's `local-path` class ignores `fsGroup`, so write permission on a real CSI class
(where the volume's root is group-owned) is proven by the render only (a test checks the fsGroup of
every Pod that mounts the volume). Check it at the first real deploy of each environment:

```bash
kubectl -n <ns>-db create job backup-check --from=cronjob/<fullname>-pg-backup
kubectl -n <ns>-db wait --for=condition=complete job/backup-check --timeout=300s
kubectl -n <ns>-db logs job/backup-check   # wants: Backup created: /backup/<name>-<timestamp>.sql.gz
kubectl -n <ns>-db delete job backup-check
```

A `Permission denied` on `/backup/...` in that log means the volume's group is not writable.

The kind job runs one backup and its verification on every pull request
([Infrastructure, Local cluster (kind)](./infrastructure.md#local-cluster-kind)). The core of the job:

```yaml
# now in the Helm chart: charts/your-service-name/templates/pg-backup-cronjob.yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: your-service-name-pg-backup
  namespace: your-service-name-db
spec:
  schedule: "0 2 * * *"   # Daily at 2 AM UTC
  jobTemplate:
    spec:
      template:
        spec:
          containers:
            - name: pg-backup
              image: postgres:16-alpine
              command:
                - /bin/sh
                - -c
                - |
                  TIMESTAMP=$(date +%Y%m%d-%H%M%S)
                  BACKUP_FILE="/backup/your-service-name-${TIMESTAMP}.sql.gz"
                  pg_dump -h "${DB_HOST}" -U "${DB_USER}" -d "${DB_NAME}" | gzip > "${BACKUP_FILE}.partial"
                  mv "${BACKUP_FILE}.partial" "${BACKUP_FILE}"
                  find /backup \( -name "*.sql.gz" -o -name "*.sql.gz.partial" \) -mtime +30 -delete
              env:
                - name: DB_HOST
                  value: "your-service-name-postgresql"
                - name: DB_USER
                  valueFrom:
                    secretKeyRef:
                      name: your-service-name-db-credentials
                      key: username
                - name: DB_NAME
                  valueFrom:
                    secretKeyRef:
                      name: your-service-name-db-credentials
                      key: database
                - name: PGPASSWORD
                  valueFrom:
                    secretKeyRef:
                      name: your-service-name-db-credentials
                      key: password
              volumeMounts:
                - name: backup-volume
                  mountPath: /backup
          volumes:
            - name: backup-volume
              persistentVolumeClaim:
                claimName: your-service-name-pg-backup-pvc
```

### NetworkPolicy for Database

Restrict database access to backend and migration pods:

```yaml
# now in the Helm chart: charts/your-service-name/templates/db-network-policy.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-backend-to-db
  namespace: your-service-name-db
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: your-service-name-postgresql
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: your-service-name
      ports:
        - protocol: TCP
          port: 5432
```
