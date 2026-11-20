# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Persistence representations (DB-agnostic entities).

Responsibilities:
- Define data structures suitable for storage and retrieval.

Public API:
- `EchoRecord`: Echo entity.
- `ItemEntity`: Item entity (SAMPLE/REFERENCE).

Operational:
- Concurrency: Plain dataclasses; thread-safe usage depends on repositories.
- Logging: None.

Usage
-----
    from your_service_name.backend.entities import EchoRecord, ItemEntity
"""

from __future__ import annotations

from .echo_record import EchoRecord
from .item import ItemEntity

__all__ = ["EchoRecord", "ItemEntity"]
