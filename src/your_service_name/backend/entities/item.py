# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
SAMPLE/REFERENCE: Item entity (DB-agnostic domain shape).

Responsibilities:
- Define the persistence-independent shape of an Item.

Public API:
- `ItemEntity`: Dataclass with id, name, description, is_active, created_at, updated_at.

Operational:
- Concurrency: Immutable dataclass; safe to share across threads.
- Logging: None.

Usage
-----
from your_service_name.backend.entities.item import ItemEntity
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True, frozen=True)
class ItemEntity:
    """
    DB-agnostic representation of an Item.

    Attributes
    ----------
    id : uuid.UUID
        Unique identifier.
    name : str
        Item name.
    description : str
        Item description.
    is_active : bool
        Whether the item is active.
    created_at : datetime
        UTC timestamp of creation.
    updated_at : datetime
        UTC timestamp of last update.
    deleted_at : datetime or None
        UTC timestamp of soft deletion (None if not deleted).
    """

    id: uuid.UUID
    name: str
    description: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
