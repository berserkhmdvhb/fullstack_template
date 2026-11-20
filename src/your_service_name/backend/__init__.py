# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Backend package wiring the FastAPI application and feature modules.

Responsibilities:
- Expose app factory and ASGI app for servers.
- Group subpackages (api, schemas, services, repositories, middleware).

Public API:
- `create_app`: Factory to construct the FastAPI application.
- `app`: ASGI application instance (for Uvicorn/Gunicorn).

Operational:
- Concurrency: Async endpoints; lifespan handles startup/shutdown.
- Logging: Configured via environment (`LOG_LEVEL`).

Usage
-----
    from your_service_name.backend import create_app

    app = create_app()
"""

from __future__ import annotations

from .main import app, create_app

__all__ = ["app", "create_app"]
