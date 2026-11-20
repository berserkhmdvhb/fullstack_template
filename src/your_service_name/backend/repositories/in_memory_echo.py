# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
In-memory repository implementation for echo data.

Responsibilities:
- Provide a minimal, dependency-free storage backend.

Public API:
- `InMemoryEchoRepository`: Save/list echo entries in memory.

Operational:
- Concurrency: Uses a thread lock to protect internal state.
- Logging: None.

Usage
-----
repo = InMemoryEchoRepository()
repo.save(text="hello", length=5)
assert repo.list() == [("hello", 5)]
"""

from __future__ import annotations

import threading
from collections.abc import Sequence

from your_service_name.backend.entities.echo_record import EchoRecord
from your_service_name.backend.repositories.base import EchoItem, EchoRepository

MSG_ERROR_INVALID_LENGTH = "[API] [ECHO] Length must be non-negative."


class InMemoryEchoRepository(EchoRepository):
    """
    Minimal thread-safe in-memory implementation of `EchoRepository`.

    Attributes
    ----------
    _items : list[EchoRecord]
        Internal storage of persisted echo records.
    _lock : threading.Lock
        Synchronization primitive for concurrent access.

    Examples
    --------
    >>> repo = InMemoryEchoRepository()
    >>> repo.save("hi", 2)
    >>> repo.list()
    [('hi', 2)]
    """

    def __init__(self) -> None:
        self._items: list[EchoRecord] = []
        self._lock: threading.Lock = threading.Lock()

    def save(self, text: str, length: int) -> None:
        """
        Persist a new echo entry.

        Parameters
        ----------
        text : str
            Echoed text.
        length : int
            Precomputed length of the text.

        Raises
        ------
        ValueError
            If `length` is negative.
        """
        if length < 0:
            raise ValueError(MSG_ERROR_INVALID_LENGTH)
        rec = EchoRecord(text=text, length=length)
        with self._lock:
            self._items.append(rec)

    def list(self) -> Sequence[EchoItem]:
        """
        Retrieve all stored echo entries.

        Returns
        -------
        Sequence[EchoItem]
            Sequence of `(text, length)` pairs.

        Notes
        -----
        - Returns a copy (not the internal list) to avoid external mutation.
        """
        with self._lock:
            return [(r.text, r.length) for r in self._items]
