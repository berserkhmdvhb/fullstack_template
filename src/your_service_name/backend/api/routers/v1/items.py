# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
SAMPLE/REFERENCE: Item CRUD endpoints.

Responsibilities:
- Wire HTTP schemas to the ItemService use case.
- Provide dependency wiring via centralized accessors.

Public API:
- `router`: APIRouter exposing GET/POST/PATCH/DELETE /items endpoints.

Operational:
- Concurrency: Stateless router; session handled by dependency.
- Logging: None (errors bubble to FastAPI).

Usage
-----
from fastapi import FastAPI
from your_service_name.backend.api.routers.v1.items import router
app.include_router(router)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from your_service_name.backend.api.accessors import get_item_service
from your_service_name.backend.auth import Principal, get_current_user
from your_service_name.backend.schemas.item import (
    ItemCreate,
    ItemListResponse,
    ItemResponse,
    ItemUpdate,
    NoNulStr,
)

# ruff: noqa: TC001 — FastAPI needs ItemService at runtime to resolve Depends() annotations.
from your_service_name.backend.services.item_service import ItemService

if TYPE_CHECKING:
    from your_service_name.backend.entities.item import ItemEntity

# The largest offset PostgreSQL takes (bigint); a page beyond it is empty, not a database error.
MAX_OFFSET = 2**63 - 1

router = APIRouter(prefix="/items", tags=["items"])


@router.get(
    "",
    response_model=ItemListResponse,
    status_code=status.HTTP_200_OK,
)
async def list_items(
    service: Annotated[ItemService, Depends(get_item_service)],
    search: Annotated[NoNulStr | None, Query(description="Filter by name/description.")] = None,
    page: Annotated[int, Query(ge=1, description="Page number (1-indexed).")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Items per page.")] = 50,
) -> ItemListResponse:
    """
    List items with optional search filter and pagination.

    Parameters
    ----------
    search : str or None
        Filter substring (case-insensitive ilike on name/description).
    page : int
        Page number (1-indexed, default 1).
    page_size : int
        Number of items per page (1-100, default 50).
    service : ItemService
        Injected dependency from centralized accessors.

    Returns
    -------
    ItemListResponse
        Paginated list of items with metadata.
    """
    offset = min((page - 1) * page_size, MAX_OFFSET)
    items, total = await service.list_items(search=search, offset=offset, limit=page_size)
    total_pages = (total + page_size - 1) // page_size if total > 0 else 0
    return ItemListResponse(
        items=[_to_response(i) for i in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/{item_id}",
    response_model=ItemResponse,
    status_code=status.HTTP_200_OK,
)
async def get_item(
    item_id: UUID,
    service: Annotated[ItemService, Depends(get_item_service)],
) -> ItemResponse:
    """
    Get a single item by ID.

    Parameters
    ----------
    item_id : UUID
        Item identifier.
    service : ItemService
        Injected dependency from centralized accessors.

    Returns
    -------
    ItemResponse

    Raises
    ------
    HTTPException 404
        If the item is not found.
    """
    item = await service.get_item(item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item {item_id} not found.",
        )
    return _to_response(item)


@router.post(
    "",
    response_model=ItemResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_item(
    payload: ItemCreate,
    response: Response,
    _current_user: Annotated[Principal, Depends(get_current_user)],
    service: Annotated[ItemService, Depends(get_item_service)],
) -> ItemResponse:
    """
    Create a new item.

    Parameters
    ----------
    payload : ItemCreate
        Request body.
    response : fastapi.Response
        Raw HTTP response for setting headers.
    _current_user : Principal
        Authenticated principal (gates writes; see backend.auth). Anonymous when
        AUTH_MODE=none, so the reference CRUD is open until auth is enabled.
    service : ItemService
        Injected dependency from centralized accessors.

    Returns
    -------
    ItemResponse
        The created item.
    """
    item = await service.create_item(
        name=payload.name,
        description=payload.description,
        is_active=payload.is_active,
    )
    response.headers["Location"] = f"/api/v1/items/{item.id}"
    return _to_response(item)


@router.patch(
    "/{item_id}",
    response_model=ItemResponse,
    status_code=status.HTTP_200_OK,
)
async def update_item(
    item_id: UUID,
    payload: ItemUpdate,
    _current_user: Annotated[Principal, Depends(get_current_user)],
    service: Annotated[ItemService, Depends(get_item_service)],
) -> ItemResponse:
    """
    Partially update an item.

    Parameters
    ----------
    item_id : UUID
        Item identifier.
    payload : ItemUpdate
        Fields to update (all optional).
    _current_user : Principal
        Authenticated principal (gates writes; see backend.auth).
    service : ItemService
        Injected dependency from centralized accessors.

    Returns
    -------
    ItemResponse

    Raises
    ------
    HTTPException 404
        If the item is not found.
    """
    fields: dict[str, Any] = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not fields:
        # Nothing to update; fetch current state
        item = await service.get_item(item_id)
        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Item {item_id} not found.",
            )
        return _to_response(item)

    item = await service.update_item(item_id, **fields)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item {item_id} not found.",
        )
    return _to_response(item)


@router.delete(
    "/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_item(
    item_id: UUID,
    _current_user: Annotated[Principal, Depends(get_current_user)],
    service: Annotated[ItemService, Depends(get_item_service)],
) -> Response:
    """
    Delete an item.

    The single canonical soft-delete: sets ``deleted_at`` so the item drops out of
    list/get. (There is no separate "archive" endpoint — delete *is* the soft-delete.)

    Parameters
    ----------
    item_id : UUID
        Item identifier.
    _current_user : Principal
        Authenticated principal (gates writes; see backend.auth).
    service : ItemService
        Injected dependency from centralized accessors.

    Returns
    -------
    Response (204 No Content)

    Raises
    ------
    HTTPException 404
        If the item is not found.
    """
    deleted = await service.delete_item(item_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item {item_id} not found.",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _to_response(entity: ItemEntity) -> ItemResponse:
    """
    Convert an ItemEntity to an ItemResponse.

    Parameters
    ----------
    entity : ItemEntity

    Returns
    -------
    ItemResponse
    """
    return ItemResponse(
        id=entity.id,
        name=entity.name,
        description=entity.description,
        is_active=entity.is_active,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
    )
