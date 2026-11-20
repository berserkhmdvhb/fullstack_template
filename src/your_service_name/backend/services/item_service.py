# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
SAMPLE/REFERENCE: ItemService — thin orchestration layer.

Responsibilities:
- Coordinate repository calls for Item CRUD operations.
- No business rules beyond coordination.

Public API:
- `ItemService`: Service with list, get, create, update, delete methods.

Operational:
- Concurrency: Stateless; repository provides synchronization.
- Logging: None.

Usage
-----
from your_service_name.backend.services.item_service import ItemService
service = ItemService(repo=SqlAlchemyItemRepository(session))
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Final

from your_service_name.backend.logging.audit import audit_logger

logger = logging.getLogger(__name__)

# The only Item fields a caller may update. This is the mass-assignment guard: the
# request schema (ItemUpdate, extra="forbid") already restricts the HTTP body, and this
# allowlist is the domain-layer belt-and-suspenders so no protected/derived field
# (id, created_at, deleted_at, …) can ever be written through update_item.
_WRITABLE_ITEM_FIELDS: Final[frozenset[str]] = frozenset({"name", "description", "is_active"})

if TYPE_CHECKING:
    from your_service_name.backend.entities.item import ItemEntity
    from your_service_name.backend.repositories.item_protocol import ItemRepository


class ItemService:
    """
    Use case for Item CRUD operations.

    Attributes
    ----------
    repo : ItemRepository
        Data access interface.
    """

    def __init__(self, repo: ItemRepository) -> None:
        self.repo: ItemRepository = repo

    async def list_items(
        self,
        search: str | None = None,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[ItemEntity], int]:
        """
        List items with optional search filter and pagination.

        Parameters
        ----------
        search : str or None
            Filter substring.
        offset : int
            Number of items to skip (0-indexed).
        limit : int
            Maximum number of items to return.

        Returns
        -------
        tuple[list[ItemEntity], int]
            List of items on the current page and total count.

        Raises
        ------
        Exception
            Propagated from repository on failure.
        """
        try:
            return await self.repo.list_items(search=search, offset=offset, limit=limit)
        except Exception:
            logger.exception(
                "Failed to list items (search=%r, offset=%d, limit=%d)", search, offset, limit
            )
            raise

    async def get_item(self, item_id: uuid.UUID) -> ItemEntity | None:
        """
        Get a single item by ID.

        Parameters
        ----------
        item_id : uuid.UUID

        Returns
        -------
        ItemEntity or None

        Raises
        ------
        Exception
            Propagated from repository on failure.
        """
        try:
            return await self.repo.get_item(item_id)
        except Exception:
            logger.exception("Failed to get item %s", item_id)
            raise

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

        Raises
        ------
        Exception
            Propagated from repository on failure.
        """
        try:
            item = await self.repo.create_item(name, description, is_active=is_active)
            audit_logger.info(
                "item.created",
                extra={
                    "item_id": str(item.id),
                    "item_name": item.name,
                    "is_active": item.is_active,
                },
            )
        except Exception:
            logger.exception("Failed to create item (name=%r)", name)
            raise
        else:
            return item

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
            Only the writable fields (``name``, ``description``, ``is_active``) are
            applied; any other key is ignored (mass-assignment protection).

        Returns
        -------
        ItemEntity or None

        Raises
        ------
        Exception
            Propagated from repository on failure.
        """
        allowed = {k: v for k, v in fields.items() if k in _WRITABLE_ITEM_FIELDS}
        try:
            item = await self.repo.update_item(item_id, **allowed)
            if item is not None:
                audit_logger.info(
                    "item.updated",
                    extra={"item_id": str(item.id), "fields": list(fields.keys())},
                )
        except Exception:
            logger.exception("Failed to update item %s", item_id)
            raise
        else:
            return item

    async def delete_item(self, item_id: uuid.UUID) -> bool:
        """
        Delete an item (soft delete — sets deleted_at).

        Parameters
        ----------
        item_id : uuid.UUID

        Returns
        -------
        bool

        Raises
        ------
        Exception
            Propagated from repository on failure.
        """
        try:
            deleted = await self.repo.delete_item(item_id)
            if deleted:
                audit_logger.info("item.deleted", extra={"item_id": str(item_id)})
        except Exception:
            logger.exception("Failed to delete item %s", item_id)
            raise
        else:
            return deleted
