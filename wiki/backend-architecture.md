# Backend Architecture

Detailed breakdown of the FastAPI backend: layered architecture, data flow, and component responsibilities.

---

## Table of Contents

- [Overview](#overview)
- [Project Layout](#project-layout)
- [Application Factory](#application-factory)
- [API Layer](#api-layer)
- [Service Layer](#service-layer)
- [Repository Layer](#repository-layer)
- [Entity Layer](#entity-layer)
- [Schema Layer](#schema-layer)
- [Database Layer](#database-layer)
- [Middleware Stack](#middleware-stack)
- [Tracing](#tracing)
- [Data Flow](#data-flow)

---

## Overview

The backend follows a **clean layered architecture** with strict dependency direction:

```
Request → Router → Schema → Service → Repository → Entity → Database
Response ← Router ← Schema ← Service ← Repository ← Entity ← Database
```

Each layer depends only on the layer below it. Routers never call repositories directly; services never touch HTTP.

```mermaid
flowchart TB
    subgraph API["API Layer"]
        Routers["Routers<br/>api/routers/v1/"]
        Schemas["Schemas<br/>Pydantic v2"]
    end

    subgraph Business["Business Layer"]
        Services["Services<br/>Business logic"]
    end

    subgraph Data["Data Access Layer"]
        Repos["Repositories<br/>Protocol-based"]
        Entities["Entities<br/>Frozen dataclasses"]
    end

    subgraph Infra["Infrastructure"]
        DB["Database<br/>SQLAlchemy 2.0"]
        MW["Middleware<br/>Security, logging, rate limit"]
    end

    Routers --> Schemas --> Services --> Repos --> Entities --> DB
    MW -.->|"Wraps all"| Routers
```

---

## Project Layout

```
src/your_service_name/
├── backend/
│   ├── main.py                 # create_app() factory + lifespan
│   ├── api/
│   │   ├── routers/
│   │   │   └── v1/             # Versioned API routers
│   │   │       ├── health.py   # GET /health, GET /healthz
│   │   │       ├── echo.py     # POST /echo, GET /echo/history
│   │   │       └── items.py    # CRUD: list, get, create, update, delete
│   │   └── accessors.py        # API access helpers
│   ├── schemas/
│   │   ├── health.py           # HealthResponse, HealthzResponse
│   │   ├── echo.py             # EchoRequest, EchoResponse, EchoHistoryResponse
│   │   └── item.py             # ItemCreate, ItemUpdate, ItemResponse, ItemListResponse
│   ├── services/
│   │   ├── echo_service.py     # EchoService (business logic)
│   │   └── item_service.py     # ItemService (CRUD operations)
│   ├── repositories/
│   │   ├── base.py             # Base repository protocol
│   │   ├── item_protocol.py    # ItemRepository ABC/Protocol
│   │   ├── sqlalchemy_item.py  # SQLAlchemy implementation
│   │   └── in_memory_echo.py   # In-memory echo repository (for testing)
│   ├── entities/
│   │   ├── echo_record.py      # EchoRecord dataclass
│   │   └── item.py             # Item dataclass
│   ├── db/
│   │   ├── engine.py           # Async engine + session factory
│   │   ├── models.py           # SQLAlchemy ORM models
│   │   └── migrations/         # Alembic migration scripts
│   ├── middleware/
│   │   ├── security.py         # CORS, security headers
│   │   ├── logging.py          # Request/response logging
│   │   └── rate_limiting.py    # per-IP rate limiter (limits)
│   └── logging/
│       └── audit.py            # Audit logging utilities
├── utils/
│   ├── versioning_api.py       # API version resolution
│   └── validators.py           # Input validators
└── scripts/
    └── seed.py                 # Database seed script
```

---

## Application Factory

The backend uses a `create_app()` factory pattern with a lifespan context manager:

```mermaid
flowchart LR
    Start["Application Start"] --> Lifespan["Lifespan Startup"]
    Lifespan --> InitDB["Initialize Database"]
    InitDB --> Ready["Application Ready"]
    Ready --> Serve["Serve Requests"]
    Serve --> Shutdown["Lifespan Shutdown"]
    Shutdown --> CloseDB["Close Database"]
```

**Key file**: `src/your_service_name/backend/main.py`

- Creates the FastAPI application with OpenAPI docs
- Registers versioned routers under `/api/{version}/`
- Configures middleware stack (CORS, rate limiting, logging)
- Manages the database engine via `@asynccontextmanager` lifespan; it does **not** migrate (see [Database operations](./database.md#database-operations))

---

## API Layer

### Routers

Each router is scoped to an API version (`v1`) and a domain:

| Router | Endpoints | Description |
|--------|-----------|-------------|
| `health.py` | `GET /health`, `GET /healthz` | Health checks (DB-aware and simple) |
| `echo.py` | `POST /echo`, `GET /echo/history` | Echo service (sample) |
| `items.py` | `GET /items`, `GET /{id}`, `POST /items`, `PATCH /{id}`, `DELETE /{id}` | Full CRUD for items |

### API Versioning

```mermaid
flowchart LR
    Req["GET /api/v1/items"] --> Resolve["Version Resolver"]
    Resolve -->|"v1"| RouterV1["v1 Router"]
    Resolve -->|"v2"| RouterV2["v2 Router (future)"]
    Resolve -->|"invalid"| Error["400 Unsupported Version"]
```

The `SUPPORTED_VERSIONS` dict in `versioning_api.py` controls which versions are active.

---

## Service Layer

Services contain **business logic** and orchestrate repository calls:

```python
# Example: ItemService
class ItemService:
    def __init__(self, repo: ItemRepositoryProtocol):
        self.repo = repo

    async def list_items(self, ...) -> list[Item]:
        return await self.repo.list_items(...)

    async def create_item(self, data: ItemCreate) -> Item:
        return await self.repo.create_item(data)
```

**Principles**:
- Services depend on repository **protocols** (not concrete implementations)
- Enables easy testing with in-memory repositories
- No HTTP knowledge — services work with domain objects only

---

## Repository Layer

Repositories handle **data access** using SQLAlchemy 2.0 async patterns:

```mermaid
flowchart TB
    Service["Service"] -->|"calls"| Repo["Repository Protocol"]
    Repo -->|"implements"| SA["SQLAlchemyItemRepository"]
    SA -->|"uses"| Session["AsyncSession"]
    Session -->|"executes"| SQL["SQL Queries"]
    SQL --> DB[("PostgreSQL")]
```

**Key patterns**:
- **Protocol-based** — `ItemRepositoryProtocol` defines the interface; `SQLAlchemyItemRepository` implements it
- **Soft delete** — Items are soft-deleted (`deleted_at` set) rather than hard-deleted
- **Search** — `ilike` filtering on name/description
- **Pagination** — Offset/limit with total count

---

## Entity Layer

Entities are **immutable dataclasses** representing domain objects:

```python
@dataclass(slots=True, frozen=True)
class Item:
    id: str
    name: str
    description: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
```

**Properties**:
- `slots=True` — Memory efficient, faster attribute access
- `frozen=True` — Immutable after creation (prevents accidental mutation)

---

## Schema Layer

Pydantic v2 models for **request validation** and **response serialization**:

| Schema | Purpose | Key Constraints |
|--------|---------|----------------|
| `ItemCreate` | POST body | `name` required (1-255 chars), `description` optional, `is_active` optional |
| `ItemUpdate` | PATCH body | All fields optional, same constraints as create |
| `ItemResponse` | GET response | All fields, `from_attributes=True` |
| `ItemListResponse` | List response | `items`, `total`, `page`, `page_size`, `total_pages` |

---

## Database Layer

### Engine

```mermaid
flowchart LR
    Env["DATABASE_URL env var"] --> Resolve["URL Resolver"]
    Resolve -->|"set"| PG["postgresql+asyncpg://..."]
    Resolve -->|"unset"| SQLite["sqlite+aiosqlite:///..."]
    PG --> Engine["AsyncEngine"]
    SQLite --> Engine
    Engine --> Session["AsyncSession"]
```

- **Primary**: PostgreSQL via `asyncpg` (async I/O)
- **Fallback**: SQLite via `aiosqlite` (when `DATABASE_URL` is not set)
- **Session management**: Yield pattern with commit/rollback in `get_session()`

### Models

SQLAlchemy ORM models map to database tables:

```python
class ItemModel(Base):
    __tablename__ = "items"

    id = Column(String(36), primary_key=True, default=uuid4)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime, nullable=True)  # Soft delete
```

### Migrations

Alembic manages schema evolution:

```bash
# Create a new migration
alembic revision --autogenerate -m "add items table"

# Apply migrations
alembic upgrade head

# Rollback one migration
alembic downgrade -1
```

---

## Middleware Stack

Middleware wraps all requests in this order, outermost first (`create_app` in `main.py`):

1. `ClientIpMiddleware`: resolves the real client address first, so everything below sees one value
2. `CorrelationIdMiddleware`: scopes the `X-Request-Id` and the `traceparent` trace id for every log line
3. `RequestSizeLimitMiddleware`: refuses an oversized body before anything reads it
4. `TrustedHostMiddleware`: refuses a `Host` that is not in `ALLOWED_HOSTS`
5. `RequestLoggingMiddleware`: method, path, status, duration and client (opt-in via `LOG_REQUESTS`)
6. security headers
7. `RateLimitMiddleware`: per-client-IP throttling
8. `CORSMiddleware`

The two refusals sit just inside the correlation middleware so their problem bodies carry the request
id, and outside everything else so a refused request costs nothing more.

The Prometheus instrumentator (`/metrics`) is added last, so it wraps the whole stack from outside. It
only records metrics and does not read the client address.

| Middleware | File | Purpose |
|-----------|------|---------|
| Client IP | `middleware/client_ip.py` | Rewrites the client address from a trusted proxy's headers; the trust rules are in [Client IP](security.md#client-ip) |
| Correlation | `middleware/correlation.py` | `X-Request-Id` and `traceparent` trace id for the request's logs |
| Request limits | `middleware/request_limits.py` | Body-size cap (413) and trusted hosts (400) |
| Logging | `middleware/logging.py` | Request/response logging (opt-in via `LOG_REQUESTS`) |
| Security | `middleware/security.py` | Security headers |
| Rate Limiting | `middleware/rate_limiting.py`, `rate_limiter.py` | Per-IP request throttling (async `limits`, memory or Redis storage, fail open: see [Rate Limiting](security.md#rate-limiting)) |
| CORS | `main.py` | CORS configuration from the `CORS_*` settings |
| Prometheus | `main.py` | Metrics exposition at `/metrics` for ServiceMonitor |

-----------|------|---------|
| Rate Limiting | `middleware/rate_limiting.py` | Per-IP request throttling (enforced in-middleware via `limits`) |
| Security | `middleware/security.py` | CORS configuration, security headers |
| Logging | `middleware/logging.py` | Request/response logging (opt-in via `LOG_REQUESTS`) |
| Prometheus | `main.py` | Metrics exposition at `/metrics` for ServiceMonitor |

---

## Tracing

Opt-in OpenTelemetry tracing over OTLP/HTTP (`observability/tracing.py`). Off by default: with
`TRACING_ENABLED=false` nothing of the SDK, the exporter, the instrumentations or `redis` is imported
(a test proves it; FastAPI itself loads the small, inert `opentelemetry` API package).

| Setting | Default | Meaning |
|---------|---------|---------|
| `TRACING_ENABLED` | `false` | Turn tracing on; needs `OTEL_EXPORTER_OTLP_ENDPOINT` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset | The collector's base URL; `/v1/traces` is appended |
| `OTEL_EXPORTER_OTLP_HEADERS` | unset | `name=value,...` for the collector (a secret: keep it in the app Secret) |
| `OTEL_SERVICE_NAME` | the package name | `service.name` |
| `TRACING_SAMPLE_RATIO` | `1.0` | Share of new traces recorded (0 to 1) |

- **What is traced.** One server span per request (the FastAPI/ASGI instrumentation) and one client
  span per database statement (the SQLAlchemy instrumentation, on the app engine). The probes and
  `/metrics` (`paths.exempt_paths`, the one list; matched as exact paths, so a route that merely ends
  in `/metrics` is traced) are never traced, and a database call outside a traced request (a
  probe's `SELECT 1`) starts no trace. Only a server span may start a trace: a future background
  worker's own root span would be dropped, so give it a sampler that admits that span kind (edit
  `_server_roots_only` in `observability/tracing.py`).
- **What is not captured.** No request or response header, no body, no query string (it is removed
  from the span's URL attributes), no SQL parameter value (the statement text keeps its
  placeholders; the app engine runs with `hide_parameters`, so an error's text has none either) and
  no SQL comment. The environment cannot switch header capture on. A database error's own message
  (for example a unique-key `DETAIL` naming the value) can still reach the logs and the trace
  backend, so treat the collector as holding the same class of data as the logs.
- **Sampling.** `ParentBased(TraceIdRatioBased(TRACING_SAMPLE_RATIO))`: a caller's `traceparent`
  sampled flag is honoured, so a client can force the recording of its own requests (the rate limit
  bounds that), and the ratio decides for new traces.
- **Export.** A `BatchSpanProcessor` exports on its own thread, so a failing, slow or unreachable
  collector costs a request nothing (a full queue drops spans). `create_app` sets it up once per
  process, so each worker has its own. The lifespan flushes and stops it within five seconds
  (`SHUTDOWN_TIMEOUT_SECONDS`).
- **One trace id.** With tracing on, the `trace_id` and `span_id` on every log line and the
  `trace_id` of a problem body come from the active span, and a valid inbound `traceparent` is
  continued, so the span, the logs and the error body carry the caller's trace id.
  `outgoing_trace_headers()` then forwards the active span as the parent. With tracing off the ids
  are the inbound `traceparent`'s, as before. See [Logging](operations-runbook.md#log-correlation--tracing).

Every value above is passed explicitly to the SDK constructors, the propagator is set in code to W3C
Trace Context (the correlation middleware parses the same header, so `OTEL_PROPAGATORS` changes
nothing) and header capture is pinned off. The SDK and the exporter still read these standard
variables themselves, which the template leaves to the operator: `OTEL_SDK_DISABLED`; the span
limits (`OTEL_SPAN_*_LIMIT`, `OTEL_ATTRIBUTE_*_LIMIT`); the exporter's TLS files
(`OTEL_EXPORTER_OTLP_CERTIFICATE`, `OTEL_EXPORTER_OTLP_CLIENT_CERTIFICATE`,
`OTEL_EXPORTER_OTLP_CLIENT_KEY`, and their `*_TRACES_*` forms) and
`OTEL_SEMCONV_STABILITY_OPT_IN`. The per-signal `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` and
`OTEL_EXPORTER_OTLP_TRACES_HEADERS` are refused at startup (they would bypass the production header
rule): use the two non-signal variables. `OTEL_RESOURCE_ATTRIBUTES` is not read. FastAPI's own
telemetry is switched off in `create_app`.

In production `OTEL_EXPORTER_OTLP_HEADERS` with an `http://` endpoint to a non-loopback host is
refused (the credentials would travel in cleartext, like Redis and the database); `http://` without
headers to an in-cluster collector stays allowed.

Database spans use the SQLAlchemy instrumentation's per-engine `EngineTracer` directly (its public
entry point is a process-wide singleton and checks SQLAlchemy < 2.1, while the locked 2.1 release
works): the listeners live on the app engine, so two traced apps in one process each keep their
spans. A test runs the real path against the lock; re-check it when the package is updated.

In the chart: `observability.tracing.*` (and `backend.networkPolicy.extraEgress` to reach the
collector on 4318). See [Security](security.md#rate-limiting) for the Redis rate-limit storage that
shares the same egress setting.

---

## Data Flow

### Create Item — Full Flow

```mermaid
sequenceDiagram
    participant C as Client
    participant R as Router (items.py)
    participant S as Schema (Pydantic)
    participant SV as Service (ItemService)
    participant RP as Repository (SQLAlchemy)
    participant D as Database

    C->>R: POST /api/v1/items {name, description}
    R->>S: Validate with ItemCreate schema
    S-->>R: Validated data
    R->>SV: create_item(validated_data)
    SV->>RP: create_item(data)
    RP->>D: INSERT INTO items ...
    D-->>RP: New row
    RP-->>SV: Item entity
    SV-->>R: Item entity
    R->>S: Serialize with ItemResponse
    S-->>R: JSON response
    R-->>C: 201 Created + Location header
```

### List Items — Full Flow

```mermaid
sequenceDiagram
    participant C as Client
    participant R as Router
    participant SV as Service
    participant RP as Repository
    participant D as Database

    C->>R: GET /api/v1/items?search=foo&page=1
    R->>SV: list_items(search, page, page_size, sort, direction)
    SV->>RP: list_items(...)
    RP->>D: SELECT ... WHERE name ILIKE '%foo%' ORDER BY ... LIMIT ... OFFSET ...
    D-->>RP: Rows
    RP-->>SV: List of Item entities + total count
    SV->>S: Serialize with ItemListResponse
    S-->>R: Paginated JSON
    R-->>C: 200 OK
```

---

## Interactive docs (Swagger UI)

`GET <API_DOCS_URL>` (default `/docs`; off in production unless `ALLOW_PRODUCTION_DOCS=true`) is the
backend's own page, not FastAPI's built-in one: the built-in page inlines its init script, which a
strict `script-src` blocks. The page (`api/docs_page.py`, routes in `api/routers/docs.py`) loads
Swagger UI's CSS and bundle from jsDelivr at one exact `swagger-ui-dist` version, each with an
`integrity="sha384-..."` hash and `crossorigin="anonymous"`, and its init script from
`<API_DOCS_URL>/init.js`. The favicon is the app's `/favicon.ico`. There is no OAuth2 redirect route.

**Updating Swagger UI.** Run `scripts/swagger-ui-sri.sh <new version>` and paste the version and the two
hashes into `api/docs_page.py`. A version bump without new hashes makes the browser refuse the files, and
the `/docs` Playwright test (`frontend/e2e/csp.spec.ts`) fails. There is deliberately no Renovate rule
for it.
