# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
SAMPLE/REFERENCE: ItemRepository Protocol (data-access contract).

Responsibilities:
- Declare the contract for Item data access operations.

Public API:
- `ItemEntity`: Re-exported for convenience.
- `ItemRepository`: Protocol defining list, get, create, update, delete.

Operational:
- Concurrency: Implementations decide thread-safety strategy.
- Logging: None.

Usage
-----
from your_service_name.backend.repositories.item_protocol import ItemRepository
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from your_service_name.backend.entities.item import ItemEntity


class ItemRepository(Protocol):
    """
    Contract for Item data access.

    Methods
    -------
    list_items(search, offset, limit) -> tuple[list[ItemEntity], int]
        Return paginated items, optionally filtered by search text.
    get_item(item_id: uuid.UUID) -> ItemEntity | None
        Return a single item by ID, or None if not found.
    create_item(name, description, is_active) -> ItemEntity
        Create and return a new item.
    update_item(item_id, **fields) -> ItemEntity | None
        Partially update an item; return None if not found.
    delete_item(item_id) -> bool
        Soft-delete an item; return True if deleted, False if not found.
    """

    async def list_items(
        self,
        search: str | None = None,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[ItemEntity], int]:
        """
        Return paginated items, optionally filtered by search text.

        Parameters
        ----------
        search : str or None
            Filter substring (case-insensitive ilike on name/description).
        offset : int
            Number of items to skip (0-indexed).
        limit : int
            Maximum number of items to return.

        Returns
        -------
        tuple[list[ItemEntity], int]
            List of items on the current page and total count (before pagination).
        """
        ...

    async def get_item(self, item_id: uuid.UUID) -> ItemEntity | None:
        """
        Return a single item by ID.

        Parameters
        ----------
        item_id : uuid.UUID

        Returns
        -------
        ItemEntity or None
        """
        ...

    async def create_item(self, name: str, description: str, *, is_active: bool) -> ItemEntity:
        """
        Create a new item.

        Parameters
        ----------
        name : str
        description : str
        is_active : bool

        Returns
        -------
        ItemEntity
        """
        ...

    async def update_item(
        self,
        item_id: uuid.UUID,
        **fields: str | bool | None,
    ) -> ItemEntity | None:
        """
        Partially update an item.

        Parameters
        ----------
        item_id : uuid.UUID
        **fields : Any
            Field names and values to update.

        Returns
        -------
        ItemEntity or None (if not found)
        """
        ...

    async def delete_item(self, item_id: uuid.UUID) -> bool:
        """
        Soft-delete an item by setting deleted_at.

        Parameters
        ----------
        item_id : uuid.UUID

        Returns
        -------
        bool
            True if deleted, False if not found.
        """
        ...
