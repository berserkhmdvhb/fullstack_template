# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
FastAPI routers and dependency wiring for v1.

Responsibilities:
- Group feature-specific v1 routers and document their availability.

Public API:
- Submodules:
  - `echo`: APIRouter for echo endpoints.
  - `health`: APIRouter for health endpoints.
  - `items`: APIRouter for item CRUD endpoints (SAMPLE/REFERENCE).

Notes
-----
- Avoid eager imports to prevent circular initialization.
- Concrete router modules are loaded in the assembly function.
"""

from __future__ import annotations

__all__ = ["echo", "health", "items"]
