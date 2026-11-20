# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Tests for Item Pydantic schemas.

Covers:
- ItemCreate: required name (min_length=1, max_length=255), optional description, is_active
- ItemUpdate: all fields optional, same constraints
- ItemResponse: from_attributes=True, all fields
- ItemListResponse: pagination fields with constraints
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from your_service_name.backend.schemas.item import (
    ItemCreate,
    ItemListResponse,
    ItemResponse,
    ItemUpdate,
)

NAME_MAX_LENGTH: int = 255
PAGE_SIZE_DEFAULT: int = 10

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_uuid() -> uuid.UUID:
    """Generate a sample UUID."""
    return uuid.uuid4()


@pytest.fixture
def sample_datetime() -> datetime:
    """Generate a sample datetime."""
    return datetime.now(UTC)


class TestItemCreate:
    """Test ItemCreate schema validation."""

    def test_valid_minimal(self) -> None:
        """Should accept minimal valid data (name only)."""
        item = ItemCreate(name="Test")

        assert item.name == "Test"
        assert item.description == ""
        assert item.is_active is True

    def test_valid_full(self) -> None:
        """Should accept all fields."""
        item = ItemCreate(name="Test", description="Description", is_active=False)

        assert item.name == "Test"
        assert item.description == "Description"
        assert item.is_active is False

    def test_name_min_length(self) -> None:
        """Should reject empty name."""
        with pytest.raises(ValidationError):
            ItemCreate(name="")

    def test_name_max_length(self) -> None:
        """Should reject name longer than 255 characters."""
        with pytest.raises(ValidationError):
            ItemCreate(name="x" * 256)

    def test_name_exactly_255_chars(self) -> None:
        """Should accept name with exactly 255 characters."""
        item = ItemCreate(name="x" * 255)
        assert len(item.name) == NAME_MAX_LENGTH

    def test_description_optional(self) -> None:
        """description should default to empty string."""
        item = ItemCreate(name="Test")
        assert item.description == ""

    def test_is_active_default_true(self) -> None:
        """is_active should default to True."""
        item = ItemCreate(name="Test")
        assert item.is_active is True

    def test_extra_fields_forbidden(self) -> None:
        """Should reject extra fields."""
        with pytest.raises(ValidationError):
            ItemCreate(name="Test", extra_field="value")  # type: ignore[call-arg]


class TestItemUpdate:
    """Test ItemUpdate schema validation."""

    def test_valid_empty(self) -> None:
        """Should accept empty update (all fields optional)."""
        item = ItemUpdate()

        assert item.name is None
        assert item.description is None
        assert item.is_active is None

    def test_valid_partial(self) -> None:
        """Should accept partial update."""
        item = ItemUpdate(name="Updated")

        assert item.name == "Updated"
        assert item.description is None
        assert item.is_active is None

    def test_valid_full(self) -> None:
        """Should accept full update."""
        item = ItemUpdate(name="Updated", description="New desc", is_active=False)

        assert item.name == "Updated"
        assert item.description == "New desc"
        assert item.is_active is False

    def test_name_min_length(self) -> None:
        """Should reject empty name."""
        with pytest.raises(ValidationError):
            ItemUpdate(name="")

    def test_name_max_length(self) -> None:
        """Should reject name longer than 255 characters."""
        with pytest.raises(ValidationError):
            ItemUpdate(name="x" * 256)

    def test_extra_fields_forbidden(self) -> None:
        """Should reject extra fields."""
        with pytest.raises(ValidationError):
            ItemUpdate(name="Test", extra_field="value")  # type: ignore[call-arg]


class TestItemResponse:
    """Test ItemResponse schema."""

    def test_valid(self, sample_uuid: uuid.UUID, sample_datetime: datetime) -> None:
        """Should accept valid item data."""
        item = ItemResponse(
            id=sample_uuid,
            name="Test",
            description="Description",
            is_active=True,
            created_at=sample_datetime,
            updated_at=sample_datetime,
        )

        assert item.id == sample_uuid
        assert item.name == "Test"
        assert item.description == "Description"
        assert item.is_active is True
        assert item.created_at == sample_datetime
        assert item.updated_at == sample_datetime
        assert item.deleted_at is None

    def test_with_deleted_at(self, sample_uuid: uuid.UUID, sample_datetime: datetime) -> None:
        """Should accept deleted_at value."""
        item = ItemResponse(
            id=sample_uuid,
            name="Test",
            description="",
            is_active=True,
            created_at=sample_datetime,
            updated_at=sample_datetime,
            deleted_at=sample_datetime,
        )

        assert item.deleted_at == sample_datetime

    def test_extra_fields_forbidden(
        self, sample_uuid: uuid.UUID, sample_datetime: datetime
    ) -> None:
        """Should reject extra fields."""
        with pytest.raises(ValidationError):
            ItemResponse(  # type: ignore[call-arg]
                id=sample_uuid,
                name="Test",
                description="",
                is_active=True,
                created_at=sample_datetime,
                updated_at=sample_datetime,
                extra_field="value",
            )


class TestItemListResponse:
    """Test ItemListResponse schema with pagination."""

    def test_valid(self, sample_uuid: uuid.UUID, sample_datetime: datetime) -> None:
        """Should accept valid list response."""
        item_resp = ItemResponse(
            id=sample_uuid,
            name="Test",
            description="",
            is_active=True,
            created_at=sample_datetime,
            updated_at=sample_datetime,
        )

        list_resp = ItemListResponse(
            items=[item_resp],
            total=1,
            page=1,
            page_size=10,
            total_pages=1,
        )

        assert len(list_resp.items) == 1
        assert list_resp.total == 1
        assert list_resp.page == 1
        assert list_resp.page_size == PAGE_SIZE_DEFAULT
        assert list_resp.total_pages == 1

    def test_total_ge_zero(self) -> None:
        """total should be >= 0."""

        with pytest.raises(ValidationError):
            ItemListResponse(
                items=[],
                total=-1,
                page=1,
                page_size=PAGE_SIZE_DEFAULT,
                total_pages=0,
            )

    def test_page_ge_one(self) -> None:
        """page should be >= 1."""

        with pytest.raises(ValidationError):
            ItemListResponse(
                items=[],
                total=0,
                page=0,
                page_size=PAGE_SIZE_DEFAULT,
                total_pages=0,
            )

    def test_page_size_ge_one(self) -> None:
        """page_size should be >= 1."""

        with pytest.raises(ValidationError):
            ItemListResponse(
                items=[],
                total=0,
                page=1,
                page_size=0,
                total_pages=0,
            )

    def test_page_size_le_100(self) -> None:
        """page_size should be <= 100."""

        with pytest.raises(ValidationError):
            ItemListResponse(
                items=[],
                total=0,
                page=1,
                page_size=101,
                total_pages=0,
            )

    def test_extra_fields_forbidden(self) -> None:
        """Should reject extra fields."""

        with pytest.raises(ValidationError):
            ItemListResponse(  # type: ignore[call-arg]
                items=[],
                total=0,
                page=1,
                page_size=PAGE_SIZE_DEFAULT,
                total_pages=0,
                extra_field="value",
            )
