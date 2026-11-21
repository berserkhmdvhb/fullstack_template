# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Minimal FastAPI application wiring routers, middleware, and lifecycle hooks.

Responsibilities
----------------
- Initialize the FastAPI application (factory pattern).
- Configure logging, CORS, and security headers middleware.
- Register feature routers via a central API assembly.
- Initialize dependency injection (DI) inside the lifespan block to ensure services are ready.

Public API
----------
- `create_app`: Construct and configure the FastAPI application.
- `app`: ASGI application instance for Uvicorn.

Operational
-----------
- Concurrency: Async endpoints; lifespan uses `asynccontextmanager` for startup/shutdown.
- Logging: Configured from the `LOG_LEVEL` setting; logs startup/shutdown events.
- Settings: every environment variable is read once, by `Settings` (see `backend/settings.py`),
  validated at app creation, and stored on `app.state.settings`.

Usage
-----
from your_service_name.backend.main import create_app
app = create_app()  # use in tests or custom ASGI servers
app = create_app(Settings(AUTH_MODE="gateway"))  # or pass explicit settings

Notes
-----
- CORS origins come from the `CORS_ORIGINS` setting (comma-separated).
- Security headers are injected via `register_security_middleware`.
- Rate limiting is configured via `register_rate_limiting` (per-IP, `limits`).
- Request logging is opt-in via the `LOG_REQUESTS` setting.
- DI initialization occurs inside `lifespan` to guarantee availability for request dependencies.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from your_service_name.backend.api import build_api_router
from your_service_name.backend.db import close_db
from your_service_name.backend.logging.correlation import RequestIdFilter
from your_service_name.backend.logging.json_formatter import JsonLogFormatter
from your_service_name.backend.middleware.client_ip import ClientIpMiddleware
from your_service_name.backend.middleware.correlation import CorrelationIdMiddleware
from your_service_name.backend.middleware.logging import RequestLoggingMiddleware
from your_service_name.backend.middleware.rate_limiting import register_rate_limiting
from your_service_name.backend.middleware.security import register_security_middleware
from your_service_name.backend.repositories.in_memory_echo import InMemoryEchoRepository
from your_service_name.backend.services.echo_service import EchoService
from your_service_name.backend.settings import Settings
from your_service_name.utils.validators import LogLevel
from your_service_name.utils.versioning_api import API_CONTRACT_VERSION

# --- Bootstrap (runs once, at import time = the process entrypoint) ----------


def _use_json_logs(settings: Settings) -> bool:
    """Emit structured JSON logs when explicitly requested (``LOG_FORMAT=json``) or in
    production, where logs are shipped to a central system and must be machine-parseable."""
    return settings.LOG_FORMAT.lower() == "json" or settings.is_production


def _configure_logging(settings: Settings) -> None:
    """Configure the root logger exactly once per process.

    ``logging.basicConfig`` is idempotent — it only installs a handler when the root
    logger has none — so this guard makes the intent explicit and safe under repeated
    import / re-entrant calls without a mutable module flag.

    Logs are structured JSON when :func:`_use_json_logs` (production, or ``LOG_FORMAT=json``);
    otherwise a readable single-line text format. Either way a :class:`RequestIdFilter` injects
    the active correlation id (``X-Request-Id``) onto every record.
    """
    if logging.getLogger().handlers:
        return
    log_level = LogLevel.resolve(settings.LOG_LEVEL)
    root = logging.getLogger()
    handler = logging.StreamHandler()
    if _use_json_logs(settings):
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s [request_id=%(request_id)s]: %(message)s",
            )
        )
    # On the HANDLER (not the logger): a logger-level filter only runs for records that logger
    # emits itself, so application loggers (children of root) would skip it and the text format's
    # ``%(request_id)s`` would raise KeyError. A handler-level filter runs for every record that
    # passes through — including child loggers propagating up — so all logs carry the id.
    handler.addFilter(RequestIdFilter())
    root.addHandler(handler)
    root.setLevel(log_level)


# ``.env`` is read by ``Settings`` itself (env_file), relative to the working directory.
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Manage application startup and shutdown.

    Enter/Exit Semantics
    --------------------
    - On enter: Initialize dependency injection objects (EchoService, repository, DB).
    - On exit : Dispose/close resources (DB engine).

    Cancellation
    ------------
    - FastAPI/Starlette manage server lifecycle; cooperative cancellation follows ASGI semantics.

    Yields
    ------
    None
        Control returns to FastAPI while the app is running.
    """
    logger.info("API starting up...")
    # Initialize DI BEFORE serving any request to avoid missing state in dependencies.
    app.state.echo_service = EchoService(repo=InMemoryEchoRepository())

    # Initialize the database engine and run migrations. init_db() retries with
    # bounded backoff and RAISES if the database stays unreachable. We do NOT let
    # that crash the process: the pod stays up (liveness stays green) but the
    # readiness probe (/health → 503) keeps it out of the Service rotation until
    # the DB is healthy — so a broken DB tier can never hide behind a green
    # /health or serve 5xx to clients (seen in an end-to-end run). In production
    # migrations normally run in the migration Job / init container before
    # Uvicorn starts; this is the in-process safety net (also used by tests).
    try:
        from your_service_name.backend.db.engine import (  # noqa: PLC0415 — scoped import to isolate DB init failure
            init_db,
        )

        await init_db(app)
    except Exception:
        logger.exception("DB initialization failed. Pod is up but NOT ready (/health → 503).")

    try:
        yield
    finally:
        await close_db(app)
        logger.info("API shutting down...")


def create_app(settings: Settings | None = None) -> FastAPI:
    """
    Construct and configure the FastAPI application.

    Parameters
    ----------
    settings : Settings or None
        Validated settings. ``None`` builds them from the environment (raising
        ``pydantic.ValidationError`` listing every invalid variable); either way they are
        stored once on ``app.state.settings``.

    Returns
    -------
    fastapi.FastAPI
        Configured application with CORS, security middleware, and routers.

    Notes
    -----
    - If `CORS_ALLOW_CREDENTIALS=true`, `"*"` is removed from `allow_origins` per browser rules.
      Ensure you provide explicit origins when credentials are enabled.
    """
    settings = settings if settings is not None else Settings()
    _configure_logging(settings)
    app = FastAPI(
        title=settings.API_TITLE,
        description=settings.API_DESCRIPTION,
        version=API_CONTRACT_VERSION,
        # Interactive Swagger docs (/docs) use FastAPI's built-in page, which loads
        # Swagger UI from the jsDelivr CDN and inlines its init script. A strict CSP
        # would blank it, so the frontend admits that CDN origin + inline script ONLY
        # on /docs via a scoped CSP relaxation (see frontend/middleware.ts). /docs is
        # env-driven so it can be disabled (set API_DOCS_URL="" if desired).
        docs_url=settings.docs_url,
        openapi_url=settings.openapi_url,
        # ReDoc is intentionally left disabled (dropped): it has the same CDN +
        # inline-script CSP problem as /docs, and /docs alone covers interactive docs.
        redoc_url=None,
        lifespan=lifespan,
        openapi_tags=[
            {"name": "health", "description": "Service health checks"},
            {"name": "echo", "description": "Echo demo endpoints"},
            {"name": "items", "description": "Item CRUD sample endpoints"},
        ],
    )
    app.state.settings = settings

    cors_origins = list(settings.CORS_ORIGINS)
    allow_credentials = settings.CORS_ALLOW_CREDENTIALS
    # If credentials are enabled, do NOT allow "*" (browser restriction).
    if allow_credentials and ("*" in cors_origins):
        logger.warning("CORS_ORIGINS contains '*' with credentials enabled; dropping '*'.")
        cors_origins = [o for o in cors_origins if o != "*"]

    # Methods/headers are env-driven and default to an explicit, non-wildcard set
    # (never ["*"]) so the template does not over-permit cross-origin requests.
    allow_methods = settings.CORS_ALLOW_METHODS
    allow_headers = settings.CORS_ALLOW_HEADERS

    app.add_middleware(
        CORSMiddleware,
        # Empty origins => same-origin only (restrictive default).
        allow_origins=cors_origins,
        allow_credentials=allow_credentials,
        allow_methods=allow_methods,
        allow_headers=allow_headers,
    )

    register_rate_limiting(app, settings)
    register_security_middleware(app)

    # Optional request logging (controlled by the LOG_REQUESTS setting).
    app.add_middleware(RequestLoggingMiddleware, log_requests=settings.LOG_REQUESTS)

    # Correlation id MUST be the outermost middleware so it scopes the id for every log emitted
    # while handling the request — including the request-logging middleware and route handlers.
    # ``add_middleware`` inserts at the front of the stack, so it is added LAST to end up outermost.
    app.add_middleware(CorrelationIdMiddleware)

    # The client IP is resolved FIRST (outermost, added last) so correlation, the request logs, the
    # rate limiter and CORS all see one address. Forwarding headers count only from a peer in
    # TRUSTED_PROXY_CIDRS; Uvicorn's own proxy-header handling is off (see the launcher).
    app.add_middleware(
        ClientIpMiddleware,
        trusted=settings.TRUSTED_PROXY_CIDRS,
        trust_cf=settings.TRUST_CF_CONNECTING_IP,
    )

    # --- Router assembly under a canonical prefix --------------------------------
    app.include_router(build_api_router(prefix="/api", version=settings.API_VERSION))

    # --- Prometheus metrics instrumentation ------------------------------------
    # Exposes /metrics endpoint for Prometheus scraping
    Instrumentator().instrument(app).expose(app, endpoint="/metrics")

    # /docs is provided by FastAPI's built-in Swagger page (docs_url above); its
    # /openapi.json is already mounted by the routers, so no extra wiring is needed.

    return app


# ASGI app for Uvicorn
app = create_app()
