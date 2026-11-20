# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Domain models capturing business concepts independent of transport/persistence.

Responsibilities:
- Encapsulate business invariants and behavior (pure Python).

Public API:
- Re-export domain objects.

Operational:
- Concurrency: Stateless/immutable preferred where possible.
- Logging: None.

Usage
-----
    from your_service_name.backend.models import Message

Notes
-----
- Keep transport concerns in `schemas/` and storage in `entities/`.
"""

from __future__ import annotations

from .message import Message

__all__ = ["Message"]
