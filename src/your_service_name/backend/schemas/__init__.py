"""
API boundary schemas (Pydantic models) for request and response payloads.

Responsibilities:
- Define data contracts exposed by the HTTP API.
- Validate and serialize external input/output.

Public API:
- Re-export commonly used schemas for convenience.

Operational:
- Concurrency: Immutable Pydantic models; safe across requests.
- Logging: None.

Usage
-----
    from your_service_name.backend.schemas import EchoRequest, EchoResponse

Notes
-----
- Keep these models transport-oriented (HTTP/JSON).
- Domain rules belong in `models/` or `services/`.
"""

from __future__ import annotations

from .echo import EchoRequest, EchoResponse
from .health import HealthStatus
from .item import ItemCreate, ItemListResponse, ItemResponse, ItemUpdate

__all__ = [
    "EchoRequest",
    "EchoResponse",
    "HealthStatus",
    "ItemCreate",
    "ItemListResponse",
    "ItemResponse",
    "ItemUpdate",
]
