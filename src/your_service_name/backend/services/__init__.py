# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Application services (use cases) orchestrating domain and repositories.

Responsibilities:
- Coordinate domain objects and persistence for business operations.

Public API:
- `EchoService`: Use case for echoing text and storing results.
- `ItemService`: Use case for Item CRUD operations (SAMPLE/REFERENCE).

Operational:
- Concurrency: Stateless; depends on repository thread-safety.
- Logging: None.

Usage
-----
    from your_service_name.backend.repositories import InMemoryEchoRepository
    from your_service_name.backend.services import EchoService

    service = EchoService(repo=InMemoryEchoRepository())
    text, length = service.echo("hello")
"""

from __future__ import annotations

from .echo_service import EchoService
from .item_service import ItemService

__all__ = ["EchoService", "ItemService"]
