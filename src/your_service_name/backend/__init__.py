# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Backend package wiring the FastAPI application and feature modules.

Responsibilities:
- Group subpackages (api, schemas, services, repositories, middleware).
- Import nothing eagerly: importing any submodule (settings, migrations, ...) must not build the
  app or validate the environment. The app lives in `backend.main`.

Public API (import from `backend.main`):
- `create_app`: Factory to construct the FastAPI application.
- `app`: ASGI application instance (for Uvicorn/Gunicorn), `your_service_name.backend.main:app`.

Operational:
- Concurrency: Async endpoints; lifespan handles startup/shutdown.
- Logging: Configured via environment (`LOG_LEVEL`).

Usage
-----
    from your_service_name.backend.main import create_app

    app = create_app()
"""
