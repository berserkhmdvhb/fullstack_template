# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Unified dependency-injection accessors for all backend services.

Responsibilities:
- Provide a single canonical location for all FastAPI dependency factories.
- Support both singleton (startup-wired) and per-request (session-scoped) services.
- Raise descriptive errors when a service has not been initialized.

Public API:
- `get_echo_service`: Return the singleton EchoService from app.state.
- `get_item_service`: Build a fresh ItemService scoped to the current DB session.

Operational:
- Concurrency: Stateless factory functions; FastAPI manages request scope.
- Logging: None (errors propagate to FastAPI's exception handling).

Notes
-----
- All endpoint dependencies use ``Annotated[ConcreteType, Depends(factory)]``.
  With ``from __future__ import annotations`` enabled globally, forward references
  in type hints are safe and no ``TYPE_CHECKING`` guards are needed for DI types.
- Singleton services (echo) are validated at request time with a clear RuntimeError.
- Session-scoped services (items) are constructed per-request from the injected
  ``DbSession``; no app.state lookup is required.
"""

from __future__ import annotations

# ruff: noqa: TC002 — FastAPI needs Request at runtime for dependency resolution.
from fastapi import Request

# ruff: noqa: TC001 — FastAPI resolves these types at runtime for Depends() and OpenAPI docs.
from your_service_name.backend.db.engine import DbSession
from your_service_name.backend.repositories.sqlalchemy_item import SqlAlchemyItemRepository
from your_service_name.backend.services.echo_service import EchoService
from your_service_name.backend.services.item_service import ItemService

MSG_ERROR_ECHO_SERVICE_NOT_INITIALIZED = (
    "EchoService not initialized; check app lifespan/startup wiring."
)


def get_echo_service(request: Request) -> EchoService:
    """
    Return the singleton EchoService from application state.

    Parameters
    ----------
    request : fastapi.Request
        Incoming request provided by FastAPI's dependency injection.

    Returns
    -------
    EchoService
        Service instance configured at startup.

    Raises
    ------
    RuntimeError
        If the EchoService has not been initialized; indicates a lifecycle wiring issue.
    """
    svc = getattr(request.app.state, "echo_service", None)
    if svc is None:
        raise RuntimeError(MSG_ERROR_ECHO_SERVICE_NOT_INITIALIZED)
    if not isinstance(svc, EchoService):
        msg = f"Expected EchoService, got {type(svc).__name__}. Check app lifespan/startup wiring."
        raise TypeError(msg)
    return svc


def get_item_service(session: DbSession) -> ItemService:
    """
    Build a fresh ItemService scoped to the current database session.

    Parameters
    ----------
    session : AsyncSession
        Injected via DbSession dependency; one per request.

    Returns
    -------
    ItemService
        Service instance backed by a SqlAlchemyItemRepository.
    """
    repo = SqlAlchemyItemRepository(session)
    return ItemService(repo)
