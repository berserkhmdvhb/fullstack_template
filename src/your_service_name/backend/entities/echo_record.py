# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Entity representing a stored echo operation.

Responsibilities:
- Capture persisted attributes of an echo result.

Public API:
- `EchoRecord`: Dataclass with `text` and `length`.

Operational:
- Concurrency: Simple dataclass; repository handles synchronization.
- Logging: None.

Usage
-----
from your_service_name.backend.entities.echo_record import EchoRecord
rec = EchoRecord(text="hello", length=5)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class EchoRecord:
    """
    Persistence-friendly representation of an echo item.

    Attributes
    ----------
    text : str
        Echoed text to store.
    length : int
        Precomputed length of the text.
    """

    text: str
    length: int
