# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Echo use case service.

Responsibilities:
- Apply domain behavior (compute length) and store the result.

Public API:
- `EchoService`: Orchestrates echo flow.

Operational:
- Concurrency: Stateless; repository provides synchronization.
- Logging: None.

Usage
-----
from your_service_name.backend.repositories.in_memory_echo import InMemoryEchoRepository
from your_service_name.backend.services.echo_service import EchoService

service = EchoService(repo=InMemoryEchoRepository())
text, length = service.echo("hello")
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from your_service_name.backend.models.message import Message

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    # Protocol import used for type checking; avoids potential circular import worries.
    from your_service_name.backend.repositories.base import EchoItem, EchoRepository


class EchoService:
    """
    Use case for echoing text and persisting the result.

    Extended Summary
    ----------------
    Accepts raw text, constructs a domain `Message`, computes its length, and
    stores `(text, length)` via the repository.

    Attributes
    ----------
    repo : EchoRepository
        Data access interface used to persist results.

    Examples
    --------
    >>> from your_service_name.backend.repositories.in_memory_echo import InMemoryEchoRepository
    >>> svc = EchoService(repo=InMemoryEchoRepository())
    >>> svc.echo("abc")
    ('abc', 3)
    """

    def __init__(self, repo: EchoRepository) -> None:
        self.repo: EchoRepository = repo

    def list_history(self) -> list[EchoItem]:
        """
        Return all stored echo entries.

        Returns
        -------
        list[EchoItem]
            List of (text, length) tuples in insertion order.

        Raises
        ------
        Exception
            Propagated from repository on failure.
        """
        try:
            return list(self.repo.list())
        except Exception:
            logger.exception("Failed to list echo history")
            raise

    def echo(self, text: str) -> EchoItem:
        """
        Echo the given text, compute its length, and persist the result.

        Parameters
        ----------
        text : str
            Arbitrary text to process.

        Returns
        -------
        EchoItem
            `(echoed_text, length)` pair.

        Raises
        ------
        Exception
            Propagated from repository on failure.

        Examples
        --------
        >>> from your_service_name.backend.repositories.in_memory_echo import InMemoryEchoRepository
        >>> svc = EchoService(repo=InMemoryEchoRepository())
        >>> svc.echo("hi")
        ('hi', 2)
        """
        try:
            msg = Message(text=text)
            length = msg.length()
            self.repo.save(text=msg.text, length=length)
        except Exception:
            logger.exception("Failed to echo text (text=%r)", text)
            raise
        else:
            return msg.text, length
