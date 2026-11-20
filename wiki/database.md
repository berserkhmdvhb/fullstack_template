# Database

PostgreSQL setup, SQLAlchemy models, Alembic migrations, soft-delete pattern, and Kubernetes database deployment.

---

## Table of Contents

- [Overview](#overview)
- [Database Configuration](#database-configuration)
- [SQLAlchemy Models](#sqlalchemy-models)
- [Engine & Session](#engine--session)
- [Alembic Migrations](#alembic-migrations)
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

```python
# src/your_service_name/backend/db/models.py

class ItemModel(Base):
    __tablename__ = "items"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    deleted_at = Column(DateTime(timezone=True), nullable=True)
```

### Column Reference

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `id` | `String(36)` | No | UUID v4 | Primary key |
| `name` | `String(255)` | No | — | Item name (1-255 chars) |
| `description` | `Text` | Yes | — | Optional description |
| `is_active` | `Boolean` | No | `True` | Active status |
| `created_at` | `DateTime` | No | `now()` | Auto-set on create |
| `updated_at` | `DateTime` | No | `now()` | Auto-updated on change |
| `deleted_at` | `DateTime` | Yes | — | Soft-delete timestamp |

---

## Engine & Session

### Engine Creation

```python
# src/your_service_name/backend/db/engine.py

def _resolve_database_url() -> str:
    """DATABASE_URL > SQLite fallback"""
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    return "sqlite+aiosqlite:///./data/items.db"

def create_engine() -> AsyncEngine:
    url = _resolve_database_url()
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

### Migration Runner (Container)

In Docker Compose, the backend runs migrations on startup:

```yaml
backend:
  command: ["/usr/bin/tini", "--", "/app/infra/db/run_migrations.sh"]
```

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
stmt = select(ItemModel).where(ItemModel.deleted_at.is_(None))
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

class SQLAlchemyItemRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_items(self, ...) -> tuple[list[Item], int]:
        # Build query with search, pagination, sorting
        stmt = select(ItemModel).where(ItemModel.deleted_at.is_(None))
        if search:
            stmt = stmt.where(
                or_(
                    ItemModel.name.ilike(f"%{search}%"),
                    ItemModel.description.ilike(f"%{search}%"),
                )
            )
        # ... pagination, sorting, execute
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
# now in the Helm chart: charts/your-service-name/templates/postgresql-statefulset.yaml (in-cluster DB, dev/testing only; k8s/ tree removed in Phase 2 Stage C)
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
  host: "<external-host>"
  port: 5432
  name: service_template
  user: app
  # password comes from an out-of-band secret (sealed-secrets / ESO / SOPS)
```

**Pros**: High availability, automated backups, managed scaling, security patches  
**Cons**: External dependency, cost, network latency

### Database Migration Job

Migrations run as a Kubernetes Job before each deployment:

```yaml
# now in the Helm chart: charts/your-service-name/templates/migration-job.yaml (post-install,post-upgrade hook)
apiVersion: batch/v1
kind: Job
metadata:
  name: your-service-name-migration
  namespace: your-service-name
spec:
  backoffLimit: 3
  activeDeadlineSeconds: 300
  template:
    spec:
      initContainers:
        - name: wait-for-db
          image: postgres:16-alpine
          command:
            - /bin/sh
            - -c
            - |
              until pg_isready -h your-service-name-postgresql.your-service-name-db.svc.cluster.local -p 5432; do
                echo "Waiting for PostgreSQL..."
                sleep 2
              done
      containers:
        - name: migration
          image: your-service-name-backend:latest
          command:
            - python
            - -m
            - your_service_name.backend.db.run_migrations
          envFrom:
            - configMapRef:
                name: your-service-name-config
            - secretRef:
                name: your-service-name-db-credentials
      restartPolicy: Never
```

### Database Backup CronJob

Automated daily backups via CronJob:

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
                  pg_dump -h "${DB_HOST}" -U "${DB_USER}" -d "${DB_NAME}" | gzip > "/backup/your-service-name-${TIMESTAMP}.sql.gz"
                  find /backup -name "*.sql.gz" -mtime +30 -delete
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
