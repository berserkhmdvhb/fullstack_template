"""
Repository interfaces and implementations for data access.

Responsibilities:
- Abstract persistence operations from business logic.

Public API:
- `EchoRepository`: Interface defining save/list operations.
- `InMemoryEchoRepository`: Minimal in-memory implementation.
- `ItemRepository`: Protocol for Item CRUD operations (SAMPLE/REFERENCE).
- `SqlAlchemyItemRepository`: SQLAlchemy implementation (SAMPLE/REFERENCE).

Operational:
- Concurrency: In-memory repository uses a thread lock for basic safety.
- Logging: None.

Usage
-----
    from your_service_name.backend.repositories import InMemoryEchoRepository

    repo = InMemoryEchoRepository()
    repo.save(text="hi", length=2)
"""

from __future__ import annotations

from .base import EchoRepository
from .in_memory_echo import InMemoryEchoRepository
from .item_protocol import ItemRepository
from .sqlalchemy_item import SqlAlchemyItemRepository

__all__ = [
    "EchoRepository",
    "InMemoryEchoRepository",
    "ItemRepository",
    "SqlAlchemyItemRepository",
]
