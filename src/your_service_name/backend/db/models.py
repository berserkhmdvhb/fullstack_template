# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
SAMPLE/REFERENCE: SQLAlchemy 2.0 declarative base and Item model.

Responsibilities:
- Define the declarative Base class.
- Define the Item ORM model.

Public API:
- `Base`: SQLAlchemy DeclarativeBase with __name__ table naming.
- `Item`: ORM model for the items table.

Operational:
- Concurrency: ORM models are read-only metadata; sessions handle concurrency.
- Logging: None.

Usage
-----
from your_service_name.backend.db.models import Base, Item
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """
    Declarative base with automatic __name__-based table naming.

    Notes
    -----
    - All models inheriting from Base use their class name (lowercased) as the table name.
    """


class Item(Base):
    """
    ORM model for the items table.

    Attributes
    ----------
    id : UUID
        Primary key (auto-generated UUID4).
    name : str
        Item name (required, max 255 chars).
    description : str
        Item description (optional, default "").
    is_active : bool
        Whether the item is active (default True).
    created_at : datetime
        UTC timestamp of creation (server-generated).
    updated_at : datetime
    """

    __tablename__ = "items"

    # A real UUID column: ``item.id`` is a genuine ``uuid.UUID`` at runtime, so the
    # Python annotation stays honest (no ``str``-typed id smuggled through a String(36)
    # column). SQLAlchemy stores it as CHAR(36) on SQLite and as native UUID on Postgres.
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
