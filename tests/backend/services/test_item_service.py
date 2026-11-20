# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
SAMPLE/REFERENCE: Unit tests for ItemService with a fake in-memory repository.

Tests all CRUD operations without requiring a database.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from your_service_name.backend.entities.item import ItemEntity
from your_service_name.backend.repositories.item_protocol import ItemRepository
from your_service_name.backend.services.item_service import ItemService

EXPECTED_ITEM_COUNT: int = 2

# Fake in-memory repository for testing


class _FakeItemRepository(ItemRepository):
    """
    In-memory fake of ItemRepository for unit testing.

    Stores items in a dict keyed by UUID.
    """

    def __init__(self) -> None:
        self._store: dict[uuid.UUID, ItemEntity] = {}

    async def list_items(
        self,
        search: str | None = None,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[ItemEntity], int]:
        items = [i for i in self._store.values() if i.deleted_at is None]
        if search:
            pattern = search.lower()
            items = [
                i for i in items if pattern in i.name.lower() or pattern in i.description.lower()
            ]
        total = len(items)
        paginated = items[offset : offset + limit]
        return paginated, total

    async def get_item(self, item_id: uuid.UUID) -> ItemEntity | None:
        item = self._store.get(item_id)
        if item is not None and item.deleted_at is not None:
            return None
        return item

    async def create_item(self, name: str, description: str, *, is_active: bool) -> ItemEntity:
        entity = ItemEntity(
            id=uuid.uuid4(),
            name=name,
            description=description,
            is_active=is_active,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self._store[entity.id] = entity
        return entity

    async def update_item(
        self,
        item_id: uuid.UUID,
        **fields: str | bool | None,
    ) -> ItemEntity | None:
        item = self._store.get(item_id)
        if item is None:
            return None
        name = fields.get("name", item.name)
        assert isinstance(name, str)
        description = fields.get("description", item.description)
        assert isinstance(description, str)
        is_active = fields.get("is_active", item.is_active)
        assert isinstance(is_active, bool)
        updated = ItemEntity(
            id=item.id,
            name=name,
            description=description,
            is_active=is_active,
            created_at=item.created_at,
            updated_at=datetime.now(UTC),
        )
        self._store[item_id] = updated
        return updated

    async def delete_item(self, item_id: uuid.UUID) -> bool:
        if item_id not in self._store:
            return False
        item = self._store[item_id]
        self._store[item_id] = ItemEntity(
            id=item.id,
            name=item.name,
            description=item.description,
            is_active=item.is_active,
            created_at=item.created_at,
            updated_at=datetime.now(UTC),
            deleted_at=datetime.now(UTC),
        )
        return True


# Fixtures


@pytest.fixture
def fake_repo() -> _FakeItemRepository:
    """Return a fresh fake repository."""
    return _FakeItemRepository()


@pytest.fixture
def service(fake_repo: _FakeItemRepository) -> ItemService:
    """Return an ItemService wired to the fake repository."""
    return ItemService(repo=fake_repo)


@pytest.mark.asyncio
async def test_list_items_empty(service: ItemService) -> None:
    """List returns empty list when no items exist."""
    items, total = await service.list_items()
    assert items == []
    assert total == 0


@pytest.mark.asyncio
async def test_list_items_returns_all(service: ItemService, fake_repo: _FakeItemRepository) -> None:
    """List returns all items when no search filter is provided."""
    await fake_repo.create_item("Alpha", "First item", is_active=True)
    await fake_repo.create_item("Beta", "Second item", is_active=False)

    items, total = await service.list_items()
    assert len(items) == EXPECTED_ITEM_COUNT
    assert total == EXPECTED_ITEM_COUNT


@pytest.mark.asyncio
async def test_list_items_search_filters(
    service: ItemService, fake_repo: _FakeItemRepository
) -> None:
    """List with search filter returns only matching items."""
    await fake_repo.create_item("Alpha", "First item", is_active=True)
    await fake_repo.create_item("Beta", "Second item", is_active=False)
    await fake_repo.create_item("Gamma", "Alpha-related", is_active=True)

    items, total = await service.list_items(search="Alpha")
    assert len(items) == EXPECTED_ITEM_COUNT
    assert total == EXPECTED_ITEM_COUNT
    assert all("alpha" in i.name.lower() or "alpha" in i.description.lower() for i in items)


@pytest.mark.asyncio
async def test_list_items_search_case_insensitive(
    service: ItemService, fake_repo: _FakeItemRepository
) -> None:
    """Search is case-insensitive."""
    await fake_repo.create_item("Alpha", "Description here", is_active=True)

    items, total = await service.list_items(search="ALPHA")
    assert len(items) == 1
    assert total == 1


@pytest.mark.asyncio
async def test_get_item_found(service: ItemService, fake_repo: _FakeItemRepository) -> None:
    """Get returns the item when it exists."""
    created = await fake_repo.create_item("Test", "Desc", is_active=True)

    result = await service.get_item(created.id)
    assert result is not None
    assert result.id == created.id
    assert result.name == "Test"


@pytest.mark.asyncio
async def test_get_item_not_found(service: ItemService) -> None:
    """Get returns None when item does not exist."""
    result = await service.get_item(uuid.uuid4())
    assert result is None


@pytest.mark.asyncio
async def test_create_item_returns_entity(service: ItemService) -> None:
    """Create returns a fully-populated ItemEntity."""
    result = await service.create_item("New Item", "A description", is_active=True)

    assert isinstance(result, ItemEntity)
    assert result.id is not None
    assert result.name == "New Item"
    assert result.description == "A description"
    assert result.is_active is True
    assert result.created_at is not None
    assert result.updated_at is not None


@pytest.mark.asyncio
async def test_create_item_stored_in_repo(
    service: ItemService, fake_repo: _FakeItemRepository
) -> None:
    """Create persists the item in the repository."""
    created = await service.create_item("Persisted", "", is_active=False)

    stored = await fake_repo.get_item(created.id)
    assert stored is not None
    assert stored.name == "Persisted"


@pytest.mark.asyncio
async def test_update_item_modifies_fields(
    service: ItemService, fake_repo: _FakeItemRepository
) -> None:
    """Update changes specified fields and preserves others."""
    created = await fake_repo.create_item("Original", "Original desc", is_active=True)

    result = await service.update_item(created.id, name="Updated", description="New desc")
    assert result is not None
    assert result.name == "Updated"
    assert result.description == "New desc"
    assert result.is_active is True  # unchanged


@pytest.mark.asyncio
async def test_update_item_partial(service: ItemService, fake_repo: _FakeItemRepository) -> None:
    """Update with only some fields preserves the rest."""
    created = await fake_repo.create_item("Original", "Desc", is_active=True)

    result = await service.update_item(created.id, is_active=False)
    assert result is not None
    assert result.name == "Original"  # unchanged
    assert result.is_active is False


@pytest.mark.asyncio
async def test_update_item_not_found(service: ItemService) -> None:
    """Update returns None when item does not exist."""
    result = await service.update_item(uuid.uuid4(), name="Nope")
    assert result is None


@pytest.mark.asyncio
async def test_update_item_ignores_non_writable_fields(
    service: ItemService, fake_repo: _FakeItemRepository
) -> None:
    """Fields outside the writable allowlist are dropped (mass-assignment guard)."""
    created = await fake_repo.create_item("Original", "Desc", is_active=True)

    result = await service.update_item(
        created.id,
        name="Updated",
        id="forged-id",  # protected field (the PK) — must be ignored
    )

    assert result is not None
    assert result.name == "Updated"
    assert result.id == created.id  # forged primary key was ignored


@pytest.mark.asyncio
async def test_delete_item_success(service: ItemService, fake_repo: _FakeItemRepository) -> None:
    """Delete returns True and removes the item."""
    created = await fake_repo.create_item("ToDelete", "", is_active=True)

    result = await service.delete_item(created.id)
    assert result is True

    stored = await fake_repo.get_item(created.id)
    assert stored is None


@pytest.mark.asyncio
async def test_delete_item_not_found(service: ItemService) -> None:
    """Delete returns False when item does not exist."""
    result = await service.delete_item(uuid.uuid4())
    assert result is False


@pytest.mark.asyncio
async def test_full_crud_lifecycle(service: ItemService) -> None:
    """
    End-to-end: create → list → get → update → delete → verify gone.
    """
    # Create
    created = await service.create_item("Lifecycle", "Test item", is_active=True)
    assert created.id is not None

    # List
    items, total = await service.list_items()
    assert len(items) == 1
    assert total == 1
    assert items[0].id == created.id

    # Get
    fetched = await service.get_item(created.id)
    assert fetched is not None
    assert fetched.name == "Lifecycle"

    # Update
    updated = await service.update_item(created.id, name="Lifecycle Updated")
    assert updated is not None
    assert updated.name == "Lifecycle Updated"

    # Delete
    deleted = await service.delete_item(created.id)
    assert deleted is True

    # Verify gone
    gone = await service.get_item(created.id)
    assert gone is None

    items_after, total_after = await service.list_items()
    assert len(items_after) == 0
    assert total_after == 0
