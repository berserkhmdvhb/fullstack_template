# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Routers package.

Responsibilities:
- Provide a namespace for versioned router packages (e.g., `v1`, `v2`).

Public API:
- Subpackages: `v1` (and future `v2`, etc.). Import concrete routers from the versioned package:
  - `your_service_name.backend.api.routers.v1.echo`
  - `your_service_name.backend.api.routers.v1.health`

Operational:
- Concurrency: N/A (namespace-only).
- Logging: None.

Usage
-----
from fastapi import FastAPI
from your_service_name.backend.api import build_api_router

app = FastAPI()
app.include_router(build_api_router(prefix="/api", version="v1"))

Notes
-----
- This module intentionally avoids eager imports (`from .v1 import ...`) to prevent circular
  initialization issues. Concrete router imports should happen inside the assembly function or
  directly at call sites requiring them.
"""

from __future__ import annotations

# Publicly expose the versioned packages without importing their contents.
__all__ = ["v1"]
