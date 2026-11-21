# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Tests for SQLAlchemy models.

Covers:
- Base class configuration
- Item model: table name, column types, nullability, defaults
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, String, Uuid
from sqlalchemy.orm import DeclarativeBase

from your_service_name.backend.db.models import Base, Item, UTCDateTime

NAME_MAX_LENGTH: int = 255

# ---------------------------------------------------------------------------
# Tests: Base class
# ---------------------------------------------------------------------------


class TestBase:
    """Test declarative base configuration."""

    def test_base_is_declarative(self) -> None:
        """Base should be a DeclarativeBase subclass."""
        assert issubclass(Base, DeclarativeBase)


# ---------------------------------------------------------------------------
# Tests: Item model
# ---------------------------------------------------------------------------


class TestItemModel:
    """Test Item ORM model definition."""

    def test_tablename(self) -> None:
        """Item should map to 'items' table."""
        assert Item.__tablename__ == "items"

    def test_id_column_type(self) -> None:
        """id column is a real UUID (honest runtime type, not a String(36) stand-in)."""
        id_col = Item.__table__.columns["id"]
        assert isinstance(id_col.type, Uuid)

    def test_id_is_primary_key(self) -> None:
        """id column should be the primary key."""
        id_col = Item.__table__.columns["id"]
        assert id_col.primary_key is True

    def test_name_column_type(self) -> None:
        """name column should be String(255)."""
        name_col = Item.__table__.columns["name"]
        assert isinstance(name_col.type, String)
        assert name_col.type.length == NAME_MAX_LENGTH

    def test_name_is_not_nullable(self) -> None:
        """name column should not be nullable."""
        name_col = Item.__table__.columns["name"]
        assert name_col.nullable is False

    def test_description_column_type(self) -> None:
        """description column should be String (unbounded)."""
        desc_col = Item.__table__.columns["description"]
        assert isinstance(desc_col.type, String)

    def test_description_is_not_nullable(self) -> None:
        """description column should not be nullable (has default)."""
        desc_col = Item.__table__.columns["description"]
        assert desc_col.nullable is False

    def test_is_active_column_type(self) -> None:
        """is_active column should be Boolean."""
        active_col = Item.__table__.columns["is_active"]
        assert isinstance(active_col.type, Boolean)

    def test_is_active_is_not_nullable(self) -> None:
        """is_active column should not be nullable."""
        active_col = Item.__table__.columns["is_active"]
        assert active_col.nullable is False

    def test_created_at_column_type(self) -> None:
        """created_at column should be UTCDateTime (timezone-aware)."""
        created_col = Item.__table__.columns["created_at"]
        assert isinstance(created_col.type, UTCDateTime)
        assert created_col.type.timezone is True

    def test_created_at_has_server_default(self) -> None:
        """created_at should have server_default (func.now())."""
        created_col = Item.__table__.columns["created_at"]
        assert created_col.server_default is not None

    def test_updated_at_column_type(self) -> None:
        """updated_at column should be UTCDateTime (timezone-aware)."""
        updated_col = Item.__table__.columns["updated_at"]
        assert isinstance(updated_col.type, UTCDateTime)
        assert updated_col.type.timezone is True

    def test_updated_at_has_onupdate(self) -> None:
        """updated_at should have onupdate (func.now())."""
        updated_col = Item.__table__.columns["updated_at"]
        assert updated_col.onupdate is not None

    def test_deleted_at_is_nullable(self) -> None:
        """deleted_at column should be nullable."""
        deleted_col = Item.__table__.columns["deleted_at"]
        assert deleted_col.nullable is True

    def test_item_can_be_instantiated(self) -> None:
        """Item should be instantiable with required fields."""
        item = Item(
            id=str(uuid.uuid4()),
            name="Test Item",
            description="Test description",
            is_active=True,
        )

        assert item.id is not None
        assert item.name == "Test Item"
        assert item.description == "Test description"
        assert item.is_active is True
