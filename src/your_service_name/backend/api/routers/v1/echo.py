# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Echo endpoints.

Responsibilities:
- Wire HTTP schemas to the `EchoService` use case.
- Provide dependency wiring via centralized accessors.

Public API:
- `router`: APIRouter exposing `POST /api/v1/echo` and `GET /api/v1/echo/history`.

Operational:
- Concurrency: Stateless router; repository is protected with a lock.
- Logging: None (errors bubble to FastAPI).

Usage
-----
from fastapi import FastAPI
from your_service_name.backend.api.routers.v1.echo import router

app = FastAPI()
app.include_router(router)
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from your_service_name.backend.api.accessors import get_echo_service
from your_service_name.backend.schemas.echo import EchoRequest, EchoResponse

# ruff: noqa: TC001 — FastAPI needs EchoService at runtime to resolve Depends() annotations.
from your_service_name.backend.services.echo_service import EchoService

router = APIRouter(prefix="/echo", tags=["echo"])


@router.post("", response_model=EchoResponse, status_code=status.HTTP_201_CREATED)
async def echo(
    payload: EchoRequest,
    response: Response,
    service: Annotated[EchoService, Depends(get_echo_service)],
) -> EchoResponse:
    """
    Create an echo record and return its representation.

    Parameters
    ----------
    payload : EchoRequest
        Request body containing the text to echo (and optional `source_url`).
    response : fastapi.Response
        Raw HTTP response used to set headers (e.g., `Location`) for 201 Created.
    service : EchoService
        Injected dependency from centralized accessors.

    Returns
    -------
    EchoResponse
        Representation of the echoed text, including its character length.

    Raises
    ------
    ValueError
        Propagated if the underlying repository rejects invalid data (e.g., negative length).

    Examples
    --------
    ```python
    >>> from fastapi.testclient import TestClient
    >>> from your_service_name.backend.main import create_app
    >>> client = TestClient(create_app())
    >>> resp = client.post("/api/v1/echo", json={"text": "hello"})
    >>> resp.status_code
    201
    >>> resp.headers["Location"].endswith("/api/v1/echo/history")
    True
    >>> resp.json()
    {'text': 'hello', 'length': 5}
    ```

    Notes
    -----
    - REST: 201 Created + `Location` header. For now, `Location` points to the collection
      (`/api/v1/echo/history`). If per-item identifiers are introduced later, update it to
      `/api/v1/echo/{id}`.
    """
    text, length = service.echo(payload.text)

    # include Location (collection for now).
    response.headers["Location"] = "/api/v1/echo/history"

    return EchoResponse(text=text, length=length)


@router.get("/history", response_model=list[EchoResponse])
async def history(
    service: Annotated[EchoService, Depends(get_echo_service)],
) -> list[EchoResponse]:
    """
    Return previously stored echo entries.

    Parameters
    ----------
    service : EchoService
        Injected dependency; repository accessed via ``service.repo``.

    Returns
    -------
    list[EchoResponse]
        History of echoed items.

    Examples
    --------
    ```python
    >>> from fastapi.testclient import TestClient
    >>> from your_service_name.backend.main import create_app
    >>> client = TestClient(create_app())
    >>> _ = client.post("/api/v1/echo", json={"text": "abc"})
    >>> _ = client.post("/api/v1/echo", json={"text": "xyz"})
    >>> resp = client.get("/api/v1/echo/history")
    >>> resp.status_code
    200
    >>> resp.json()
    [{'text': 'abc', 'length': 3}, {'text': 'xyz', 'length': 3}]
    ```
    """
    items: Sequence[tuple[str, int]] = service.list_history()
    return [EchoResponse(text=t, length=n) for (t, n) in items]
