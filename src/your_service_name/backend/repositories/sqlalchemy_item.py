# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
SAMPLE/REFERENCE: SQLAlchemy ItemRepository implementation.

Responsibilities:
- Implement the ItemRepository Protocol using SQLAlchemy 2.0 async session.

Public API:
- `SqlAlchemyItemRepository`: Async repository backed by an AsyncSession.

Operational:
- Concurrency: Relies on the AsyncSession's connection management.
- Logging: None.

Usage
-----
from your_service_name.backend.repositories.sqlalchemy_item import SqlAlchemyItemRepository
repo = SqlAlchemyItemRepository(session)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from your_service_name.backend.db.models import Item
from your_service_name.backend.entities.item import ItemEntity
from your_service_name.backend.repositories.item_protocol import ItemRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class SqlAlchemyItemRepository(ItemRepository):
    """
    SQLAlchemy 2.0 async implementation of ItemRepository.

    Attributes
    ----------
    _session : AsyncSession
        The async database session.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _to_entity(item: Item) -> ItemEntity:
        """Convert an ORM Item to an ItemEntity."""
        return ItemEntity(
            id=item.id,
            name=item.name,
            description=item.description,
            is_active=item.is_active,
            created_at=item.created_at,
            updated_at=item.updated_at,
            deleted_at=item.deleted_at,
        )

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
            List of items on the current page and total count.
        """
        # Build the base query with optional search filter (exclude soft-deleted).
        base_stmt = select(Item).where(Item.deleted_at.is_(None))
        if search:
            pattern = f"%{search}%"
            base_stmt = base_stmt.where(Item.name.ilike(pattern) | Item.description.ilike(pattern))

        # Count total items (before pagination).
        count_stmt = select(func.count()).select_from(Item).where(Item.deleted_at.is_(None))
        if search:
            count_stmt = count_stmt.where(
                Item.name.ilike(pattern) | Item.description.ilike(pattern)
            )
        count_result = await self._session.execute(count_stmt)
        total = count_result.scalar() or 0

        # Apply pagination.
        stmt = base_stmt.order_by(Item.created_at.desc()).offset(offset).limit(limit)
        result = await self._session.execute(stmt)
        items = result.scalars().all()
        return [self._to_entity(i) for i in items], total

    async def get_item(self, item_id: uuid.UUID) -> ItemEntity | None:
        """
        Return a single item by ID (excludes soft-deleted items).

        Parameters
        ----------
        item_id : uuid.UUID

        Returns
        -------
        ItemEntity or None
        """
        stmt = select(Item).where(Item.id == item_id, Item.deleted_at.is_(None))
        result = await self._session.execute(stmt)
        item = result.scalar_one_or_none()
        return self._to_entity(item) if item else None

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
        item = Item(
            name=name,
            description=description,
            is_active=is_active,
        )
        self._session.add(item)
        await self._session.flush()
        await self._session.refresh(item)
        return self._to_entity(item)

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
        stmt = select(Item).where(Item.id == item_id)
        result = await self._session.execute(stmt)
        item = result.scalar_one_or_none()
        if item is None:
            return None
        # Fields are pre-filtered to the writable allowlist by ItemService before they
        # reach here; apply exactly those (no hasattr sweep, which would let a caller
        # mutate protected columns such as deleted_at or id).
        for key, value in fields.items():
            setattr(item, key, value)
        await self._session.flush()
        await self._session.refresh(item)
        return self._to_entity(item)

    async def delete_item(self, item_id: uuid.UUID) -> bool:
        """
        Soft-delete an item by setting deleted_at timestamp.

        Parameters
        ----------
        item_id : uuid.UUID

        Returns
        -------
        bool
            True if deleted, False if not found.
        """
        stmt = select(Item).where(Item.id == item_id, Item.deleted_at.is_(None))
        result = await self._session.execute(stmt)
        item = result.scalar_one_or_none()
        if item is None:
            return False
        item.deleted_at = datetime.now(UTC)
        await self._session.flush()
        return True
