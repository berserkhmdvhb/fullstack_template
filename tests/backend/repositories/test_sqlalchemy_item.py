# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Tests for SqlAlchemyItemRepository.

Covers:
- All 5 methods: list_items, get_item, create_item, update_item, delete_item
- Search filtering (ilike), soft-delete (delete_item), pagination, UUID typing
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from your_service_name.backend.db.models import Base
from your_service_name.backend.entities.item import ItemEntity
from your_service_name.backend.repositories.sqlalchemy_item import SqlAlchemyItemRepository

EXPECTED_ITEMS_COUNT: int = 2
EXPECTED_TOTAL_COUNT: int = 5

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Create an in-memory SQLite database session for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)  # type: ignore[call-overload]

    async with async_session_factory() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def repo(db_session: AsyncSession) -> SqlAlchemyItemRepository:
    """Create a SqlAlchemyItemRepository with test session."""
    return SqlAlchemyItemRepository(db_session)


@pytest.fixture
def sample_item_data() -> dict[str, object]:
    """Sample item data for testing."""
    return {
        "name": "Test Item",
        "description": "Test description",
        "is_active": True,
    }


class TestCreateItem:
    """Test item creation."""

    @pytest.mark.asyncio
    async def test_create_item(
        self, repo: SqlAlchemyItemRepository, sample_item_data: dict[str, object]
    ) -> None:
        """Should create a new item."""
        item = await repo.create_item(**sample_item_data)  # type: ignore[arg-type]

        assert isinstance(item, ItemEntity)
        assert item.name == "Test Item"
        assert item.description == "Test description"
        assert item.is_active is True
        assert item.id is not None
        # The id column is a real UUID, so the runtime type matches the annotation
        # (uuid.UUID, not a str smuggled through a String column).
        assert type(item.id) is uuid.UUID
        assert item.created_at is not None
        assert item.updated_at is not None
        assert item.deleted_at is None

    @pytest.mark.asyncio
    async def test_create_item_with_defaults(self, repo: SqlAlchemyItemRepository) -> None:
        """Should use defaults for optional fields."""
        item = await repo.create_item(name="Minimal", description="", is_active=True)

        assert item.name == "Minimal"
        assert item.description == ""
        assert item.is_active is True


class TestListItems:
    """Test item listing."""

    @pytest.mark.asyncio
    async def test_list_items_empty(self, repo: SqlAlchemyItemRepository) -> None:
        """Should return empty list when no items."""
        items, total = await repo.list_items()

        assert items == []
        assert total == 0

    @pytest.mark.asyncio
    async def test_list_items_with_items(
        self, repo: SqlAlchemyItemRepository, sample_item_data: dict[str, object]
    ) -> None:
        """Should return all items."""
        await repo.create_item(**sample_item_data)  # type: ignore[arg-type]
        await repo.create_item(name="Second Item", description="", is_active=True)

        items, total = await repo.list_items()

        assert len(items) == EXPECTED_ITEMS_COUNT
        assert total == EXPECTED_ITEMS_COUNT

    @pytest.mark.asyncio
    async def test_list_items_excludes_deleted(
        self, repo: SqlAlchemyItemRepository, sample_item_data: dict[str, object]
    ) -> None:
        """Should exclude soft-deleted items."""
        item = await repo.create_item(**sample_item_data)  # type: ignore[arg-type]
        await repo.delete_item(item.id)

        items, total = await repo.list_items()

        assert len(items) == 0
        assert total == 0

    @pytest.mark.asyncio
    async def test_list_items_with_search(self, repo: SqlAlchemyItemRepository) -> None:
        """Should filter by search term."""
        await repo.create_item(name="Alpha Item", description="First", is_active=True)
        await repo.create_item(name="Beta Item", description="Second", is_active=True)
        await repo.create_item(name="Gamma Item", description="Third", is_active=True)

        items, total = await repo.list_items(search="alpha")

        assert len(items) == 1
        assert total == 1
        assert items[0].name == "Alpha Item"

    @pytest.mark.asyncio
    async def test_list_items_with_pagination(self, repo: SqlAlchemyItemRepository) -> None:
        """Should support pagination."""
        for i in range(5):
            await repo.create_item(name=f"Item {i}", description="", is_active=True)

        items, total = await repo.list_items(offset=1, limit=2)

        assert len(items) == EXPECTED_ITEMS_COUNT
        assert total == EXPECTED_TOTAL_COUNT


class TestGetItem:
    """Test item retrieval."""

    @pytest.mark.asyncio
    async def test_get_item_found(
        self, repo: SqlAlchemyItemRepository, sample_item_data: dict[str, object]
    ) -> None:
        """Should return item when found."""
        created = await repo.create_item(**sample_item_data)  # type: ignore[arg-type]

        item = await repo.get_item(created.id)

        assert item is not None
        assert item.id == created.id
        assert item.name == "Test Item"

    @pytest.mark.asyncio
    async def test_get_item_not_found(self, repo: SqlAlchemyItemRepository) -> None:
        """Should return None when not found."""
        item = await repo.get_item(uuid.uuid4())

        assert item is None

    @pytest.mark.asyncio
    async def test_get_item_excludes_deleted(
        self, repo: SqlAlchemyItemRepository, sample_item_data: dict[str, object]
    ) -> None:
        """Should return None for soft-deleted items."""
        created = await repo.create_item(**sample_item_data)  # type: ignore[arg-type]
        await repo.delete_item(created.id)

        item = await repo.get_item(created.id)

        assert item is None


class TestUpdateItem:
    """Test item update."""

    @pytest.mark.asyncio
    async def test_update_item(
        self, repo: SqlAlchemyItemRepository, sample_item_data: dict[str, object]
    ) -> None:
        """Should update item fields."""
        created = await repo.create_item(**sample_item_data)  # type: ignore[arg-type]

        updated = await repo.update_item(
            created.id,
            name="Updated Name",
            description="Updated description",
            is_active=False,
        )

        assert updated.name == "Updated Name"  # type: ignore[union-attr]
        assert updated.description == "Updated description"  # type: ignore[union-attr]
        assert updated.is_active is False  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_update_item_partial(
        self, repo: SqlAlchemyItemRepository, sample_item_data: dict[str, object]
    ) -> None:
        """Should support partial updates."""
        created = await repo.create_item(**sample_item_data)  # type: ignore[arg-type]

        updated = await repo.update_item(created.id, name="Only Name Changed")

        assert updated.name == "Only Name Changed"  # type: ignore[union-attr]
        assert updated.description == "Test description"  # type: ignore[union-attr]
        assert updated.is_active is True  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_update_item_not_found(self, repo: SqlAlchemyItemRepository) -> None:
        """Should return None when item not found."""
        updated = await repo.update_item(uuid.uuid4(), name="Test")

        assert updated is None


class TestDeleteItem:
    """Test item deletion (the single canonical soft-delete)."""

    @pytest.mark.asyncio
    async def test_delete_item(
        self, repo: SqlAlchemyItemRepository, sample_item_data: dict[str, object]
    ) -> None:
        """Should delete an item."""
        created = await repo.create_item(**sample_item_data)  # type: ignore[arg-type]

        result = await repo.delete_item(created.id)

        assert result is True

        # Verify item is gone
        item = await repo.get_item(created.id)
        assert item is None

    @pytest.mark.asyncio
    async def test_delete_item_not_found(self, repo: SqlAlchemyItemRepository) -> None:
        """Should return False when item not found."""
        result = await repo.delete_item(uuid.uuid4())

        assert result is False
