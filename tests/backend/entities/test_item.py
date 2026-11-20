# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Tests for ItemEntity dataclass.

Covers:
- Dataclass with slots=True, frozen=True
- All fields: id, name, description, is_active, created_at, updated_at, deleted_at
- Frozen: cannot modify after creation
- Slots: no __dict__
"""

from __future__ import annotations

import dataclasses
import uuid
from dataclasses import is_dataclass
from datetime import UTC, datetime

import pytest

from your_service_name.backend.entities.item import ItemEntity

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_item() -> ItemEntity:
    """Create a sample ItemEntity for testing."""
    now = datetime.now(UTC)
    return ItemEntity(
        id=uuid.uuid4(),
        name="Test Item",
        description="Test description",
        is_active=True,
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# Tests: Dataclass configuration
# ---------------------------------------------------------------------------


class TestItemEntityConfiguration:
    """Test ItemEntity dataclass configuration."""

    def test_is_dataclass(self) -> None:
        """ItemEntity should be a dataclass."""
        assert is_dataclass(ItemEntity)

    def test_has_slots(self) -> None:
        """ItemEntity should use slots (no __dict__)."""
        item = ItemEntity(
            id=uuid.uuid4(),
            name="Test",
            description="",
            is_active=True,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        assert not hasattr(item, "__dict__")

    def test_is_frozen(self) -> None:
        """ItemEntity should be frozen (immutable)."""
        item = ItemEntity(
            id=uuid.uuid4(),
            name="Test",
            description="",
            is_active=True,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        with pytest.raises(dataclasses.FrozenInstanceError):
            item.name = "Modified"  # type: ignore[misc]


class TestItemEntityFields:
    """Test ItemEntity field definitions."""

    def test_has_id_field(self, sample_item: ItemEntity) -> None:
        """ItemEntity should have id field of type UUID."""
        assert isinstance(sample_item.id, uuid.UUID)

    def test_has_name_field(self, sample_item: ItemEntity) -> None:
        """ItemEntity should have name field of type str."""
        assert isinstance(sample_item.name, str)

    def test_has_description_field(self, sample_item: ItemEntity) -> None:
        """ItemEntity should have description field of type str."""
        assert isinstance(sample_item.description, str)

    def test_has_is_active_field(self, sample_item: ItemEntity) -> None:
        """ItemEntity should have is_active field of type bool."""
        assert isinstance(sample_item.is_active, bool)

    def test_has_created_at_field(self, sample_item: ItemEntity) -> None:
        """ItemEntity should have created_at field of type datetime."""
        assert isinstance(sample_item.created_at, datetime)

    def test_has_updated_at_field(self, sample_item: ItemEntity) -> None:
        """ItemEntity should have updated_at field of type datetime."""
        assert isinstance(sample_item.updated_at, datetime)

    def test_has_deleted_at_field(self, sample_item: ItemEntity) -> None:
        """ItemEntity should have deleted_at field (optional)."""
        assert sample_item.deleted_at is None

    def test_deleted_at_can_be_set(self) -> None:
        """deleted_at should accept a datetime value."""
        now = datetime.now(UTC)
        item = ItemEntity(
            id=uuid.uuid4(),
            name="Test",
            description="",
            is_active=True,
            created_at=now,
            updated_at=now,
            deleted_at=now,
        )

        assert item.deleted_at == now


class TestItemEntityImmutability:
    """Test ItemEntity immutability (frozen dataclass)."""

    def test_cannot_modify_name(self, sample_item: ItemEntity) -> None:
        """Should not be able to modify name after creation."""
        with pytest.raises(dataclasses.FrozenInstanceError):
            sample_item.name = "New Name"  # type: ignore[misc]

    def test_cannot_modify_description(self, sample_item: ItemEntity) -> None:
        """Should not be able to modify description after creation."""
        with pytest.raises(dataclasses.FrozenInstanceError):
            sample_item.description = "New description"  # type: ignore[misc]

    def test_cannot_modify_is_active(self, sample_item: ItemEntity) -> None:
        """Should not be able to modify is_active after creation."""
        with pytest.raises(dataclasses.FrozenInstanceError):
            sample_item.is_active = False  # type: ignore[misc]


class TestItemEntityEquality:
    """Test ItemEntity equality behavior."""

    def test_equal_items_are_equal(self) -> None:
        """Two items with same fields should be equal."""
        now = datetime.now(UTC)
        item_id = uuid.uuid4()

        item1 = ItemEntity(
            id=item_id,
            name="Test",
            description="",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        item2 = ItemEntity(
            id=item_id,
            name="Test",
            description="",
            is_active=True,
            created_at=now,
            updated_at=now,
        )

        assert item1 == item2

    def test_different_items_are_not_equal(self) -> None:
        """Two items with different fields should not be equal."""
        now = datetime.now(UTC)

        item1 = ItemEntity(
            id=uuid.uuid4(),
            name="Test 1",
            description="",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        item2 = ItemEntity(
            id=uuid.uuid4(),
            name="Test 2",
            description="",
            is_active=True,
            created_at=now,
            updated_at=now,
        )

        assert item1 != item2
