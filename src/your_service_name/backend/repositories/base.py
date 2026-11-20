# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Repository interface definitions.

Responsibilities:
- Declare contracts for data access operations.

Public API:
- `EchoRepository`: Protocol for echo data persistence.

Operational:
- Concurrency: Implementations decide thread-safety strategy.
- Logging: None.

Usage
-----
from your_service_name.backend.repositories.base import EchoRepository
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

EchoItem = tuple[str, int]


class EchoRepository(Protocol):
    """
    Contract for storing and retrieving echo results.

    Methods
    -------
    save(text: str, length: int) -> None
        Persist a new echo entry.
    list() -> Sequence[EchoItem]
        Return all stored echo entries.
    """

    def save(self, text: str, length: int) -> None:
        """
        Persist a new echo entry.

        Parameters
        ----------
        text : str
            Echoed text.
        length : int
            Precomputed length of the text.
        """
        ...

    def list(self) -> Sequence[EchoItem]:
        """
        Retrieve all stored echo entries.

        Returns
        -------
        Sequence[EchoItem]
            Sequence of `(text, length)` pairs.
        """
        ...
