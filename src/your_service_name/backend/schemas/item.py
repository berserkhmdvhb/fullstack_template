# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
SAMPLE/REFERENCE: Item endpoint request/response schemas.

Responsibilities:
- Validate incoming create/update payloads.
- Serialize item data for API responses.

Public API:
- `ItemCreate`: Request body for creating an item.
- `ItemUpdate`: Request body for updating an item (all fields optional).
- `ItemResponse`: Response payload for a single item.
- `ItemListResponse`: Response payload for a list of items.

Operational:
- Concurrency: Safe; pure data containers.
- Logging: None.

Usage
-----
from your_service_name.backend.schemas.item import ItemCreate, ItemResponse
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field


def reject_nul(value: str) -> str:
    """Refuse a NUL character: PostgreSQL text cannot hold one, so it would be a 500, not a 422.

    A validator rather than a ``pattern``: the published OpenAPI document does not change.
    """
    if "\x00" in value:
        msg = "must not contain a NUL character"
        raise ValueError(msg)
    return value


NoNulStr = Annotated[str, AfterValidator(reject_nul)]


class ItemCreate(BaseModel):
    """
    Request body for creating an item.

    Attributes
    ----------
    name : str
        Item name (required, 1-255 chars).
    description : str
        Item description (optional, default "").
    is_active : bool
        Whether the item is active (default True).

    Examples
    --------
    >>> ItemCreate(name="Test").model_dump()
    {'name': 'Test', 'description': '', 'is_active': True}
    """

    name: NoNulStr = Field(..., min_length=1, max_length=255, description="Item name.")
    description: NoNulStr = Field(default="", description="Item description.")
    is_active: bool = Field(default=True, description="Whether the item is active.")

    model_config = {
        "extra": "forbid",
    }


class ItemUpdate(BaseModel):
    """
    Request body for updating an item (all fields optional).

    Attributes
    ----------
    name : str or None
        New item name.
    description : str or None
        New description.
    is_active : bool or None
        New active status.

    Examples
    --------
    >>> ItemUpdate(name="Updated").model_dump()
    {'name': 'Updated', 'description': None, 'is_active': None}
    """

    name: NoNulStr | None = Field(
        default=None, min_length=1, max_length=255, description="Item name."
    )
    description: NoNulStr | None = Field(default=None, description="Item description.")
    is_active: bool | None = Field(default=None, description="Whether the item is active.")

    model_config = {
        "extra": "forbid",
    }


class ItemResponse(BaseModel):
    """
    Response payload for a single item.

    Attributes
    ----------
    id : uuid.UUID
        Item ID.
    name : str
        Item name.
    description : str
        Item description.
    is_active : bool
        Whether the item is active.
    created_at : datetime
        Creation timestamp.
    updated_at : datetime
        Last update timestamp.
    deleted_at : datetime or None
        Soft deletion timestamp (None if not deleted).

    Examples
    --------
    >>> ItemResponse(id=uuid.uuid4(), name="Test", description="", is_active=True,
    ...              created_at=datetime.now(), updated_at=datetime.now()).model_dump()
    """

    id: uuid.UUID = Field(..., description="Item ID.")
    name: str = Field(..., description="Item name.")
    description: str = Field(..., description="Item description.")
    is_active: bool = Field(..., description="Whether the item is active.")
    created_at: datetime = Field(..., description="Creation timestamp.")
    updated_at: datetime = Field(..., description="Last update timestamp.")
    deleted_at: datetime | None = Field(default=None, description="Soft deletion timestamp.")

    model_config = {
        "from_attributes": True,
        "extra": "forbid",
    }


class ItemListResponse(BaseModel):
    """
    Response payload for a list of items with pagination.

    Attributes
    ----------
    items : list[ItemResponse]
        List of items on the current page.
    total : int
        Total count of items across all pages.
    page : int
        Current page number (1-indexed).
    page_size : int
        Number of items per page.
    total_pages : int
        Total number of pages.
    """

    items: list[ItemResponse] = Field(..., description="List of items on the current page.")
    total: int = Field(..., ge=0, description="Total count of items across all pages.")
    page: int = Field(..., ge=1, description="Current page number (1-indexed).")
    page_size: int = Field(..., ge=1, le=100, description="Number of items per page.")
    total_pages: int = Field(..., ge=0, description="Total number of pages.")

    model_config = {
        "extra": "forbid",
    }


# Ensure models are fully defined for Pydantic v2/OpenAPI generation.
ItemCreate.model_rebuild()
ItemUpdate.model_rebuild()
ItemResponse.model_rebuild()
ItemListResponse.model_rebuild()
