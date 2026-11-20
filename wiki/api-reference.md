# API Reference

Complete API documentation for all endpoints, request/response schemas, and usage examples.

---

## Table of Contents

- [Base URLs](#base-urls)
- [Authentication](#authentication)
- [Health Endpoints](#health-endpoints)
- [Echo Endpoints](#echo-endpoints)
- [Item CRUD Endpoints](#item-crud-endpoints)
- [Error Responses](#error-responses)
- [OpenAPI Documentation](#openapi-documentation)

---

## Base URLs

| Environment | URL |
|------------|-----|
| **Local (direct backend)** | `http://127.0.0.1:${BACKEND_PORT}` |
| **Kubernetes (via Ingress)** | `https://api.your-domain.example` |
| **API prefix** | `/api/${API_VERSION}` |

Default: `http://127.0.0.1:8000/api/v1` or `https://api.your-domain.example/api/v1`

---

## Authentication

The API currently does **not require authentication**. All endpoints are publicly accessible.

> **Note**: For production deployments, add authentication middleware (e.g., OAuth2, JWT) as needed.

---

## Health Endpoints

### GET `/api/v1/health`

Database-aware health check.

**Response**: `200 OK`

```json
{
  "ok": true
}
```

**Response**: `200 OK` (degraded)

```json
{
  "ok": false,
  "detail": "Database not initialized"
}
```

### GET `/api/v1/healthz`

Simple connectivity check. Always returns ok.

**Response**: `200 OK`

```
ok
```

---

## Echo Endpoints

### POST `/api/v1/echo`

Echo back the provided text with its length.

**Request**:

```json
{
  "text": "Hello, World!"
}
```

**Response**: `201 Created`

```json
{
  "text": "Hello, World!",
  "length": 13
}
```

**Headers**:

| Header | Value |
|--------|-------|
| `Location` | `/api/v1/echo/history` |

**Errors**:

| Status | Condition |
|--------|-----------|
| `422` | Empty text or text exceeds max length |

### GET `/api/v1/echo/history`

List all echo records.

**Response**: `200 OK`

```json
[
  {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "text": "Hello, World!",
    "length": 13,
    "created_at": "2024-01-15T10:30:00Z"
  }
]
```

---

## Item CRUD Endpoints

### GET `/api/v1/items`

List items with pagination, search, and sorting.

**Query Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `search` | string | — | Filter by name/description (case-insensitive) |
| `page` | integer | 1 | Page number (1-indexed) |
| `page_size` | integer | 20 | Items per page |
| `sort_field` | string | `created_at` | Sort by: `name`, `created_at`, `is_active` |
| `sort_direction` | string | `desc` | `asc` or `desc` |

**Response**: `200 OK`

```json
{
  "items": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "name": "Test Item",
      "description": "A test item",
      "is_active": true,
      "created_at": "2024-01-15T10:30:00Z",
      "updated_at": "2024-01-15T10:30:00Z",
      "deleted_at": null
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 20,
  "total_pages": 1
}
```

### GET `/api/v1/items/{id}`

Get a single item by ID.

**Path Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | string (UUID) | Item ID |

**Response**: `200 OK`

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "Test Item",
  "description": "A test item",
  "is_active": true,
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:30:00Z",
  "deleted_at": null
}
```

**Errors**:

| Status | Condition |
|--------|-----------|
| `404` | Item not found (or soft-deleted) |

### POST `/api/v1/items`

Create a new item.

**Request**:

```json
{
  "name": "New Item",
  "description": "Optional description",
  "is_active": true
}
```

**Field Constraints**:

| Field | Type | Required | Constraints |
|-------|------|----------|-------------|
| `name` | string | Yes | 1–255 characters |
| `description` | string | No | — |
| `is_active` | boolean | No | Default: `true` |

**Response**: `201 Created`

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "New Item",
  "description": "Optional description",
  "is_active": true,
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:30:00Z",
  "deleted_at": null
}
```

**Headers**:

| Header | Value |
|--------|-------|
| `Location` | `/api/v1/items/{id}` |

**Errors**:

| Status | Condition |
|--------|-----------|
| `422` | Validation error (e.g., empty name, name too long) |

### PATCH `/api/v1/items/{id}`

Update an existing item. All fields are optional — only provided fields are updated.

**Path Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | string (UUID) | Item ID |

**Request**:

```json
{
  "name": "Updated Name",
  "description": null,
  "is_active": false
}
```

> **Note**: Send `null` to clear a field, or omit the field to leave it unchanged.

**Response**: `200 OK`

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "Updated Name",
  "description": null,
  "is_active": false,
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T11:00:00Z",
  "deleted_at": null
}
```

**Errors**:

| Status | Condition |
|--------|-----------|
| `404` | Item not found (or soft-deleted) |
| `422` | Validation error |

### DELETE `/api/v1/items/{id}`

Soft-delete an item (sets `deleted_at` timestamp).

**Path Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | string (UUID) | Item ID |

**Response**: `204 No Content`

**Errors**:

| Status | Condition |
|--------|-----------|
| `404` | Item not found (or already deleted) |

---

## Error Responses

### 400 Bad Request

```json
{
  "detail": "Unsupported API version: v99"
}
```

### 404 Not Found

```json
{
  "detail": "Item not found"
}
```

### 422 Unprocessable Entity (Validation Error)

```json
{
  "detail": [
    {
      "type": "string_too_short",
      "loc": ["body", "name"],
      "msg": "String should have at least 1 character",
      "input": ""
    }
  ]
}
```

### 429 Too Many Requests

```json
{
  "detail": "Rate limit exceeded"
}
```

**Headers**:

| Header | Value |
|--------|-------|
| `Retry-After` | Seconds until rate limit resets |

---

## OpenAPI Documentation

The backend exposes interactive API documentation plus the machine-readable schema
(the table below). **Swagger UI** (`/docs`) is FastAPI's built-in page; it loads
Swagger UI from the jsDelivr CDN and inlines its init script. Because the frontend
serves every page under a strict nonce CSP, it admits that CDN origin and inline
script **only on `/docs`** — every other page stays CDN-free and nonce-only (see
[Security: CSP](security.md#csp-details) and `frontend/middleware.ts`).
Set `API_DOCS_URL=` (empty) to disable `/docs`. ReDoc is intentionally not shipped
(same CDN + inline-script CSP problem; `/docs` covers interactive docs).

| Tool | URL (Local) | URL (Kubernetes) |
|------|-------------|-------------------|
| **Swagger UI** | `http://localhost:${BACKEND_PORT}/docs` | `https://api.your-domain.example/docs` |
| **OpenAPI JSON** | `http://localhost:${BACKEND_PORT}/openapi.json` | `https://api.your-domain.example/openapi.json` |
| **Metrics** | `http://localhost:${BACKEND_PORT}/metrics` | `https://api.your-domain.example/metrics` |

The frontend uses `openapi.json` to generate typed TypeScript API client code:

```bash
npm run openapi-gen
# Reads: frontend/lib/api/openapi.json
# Writes: frontend/lib/api/types.gen.d.ts
```
