# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Domain model representing a textual message.

Responsibilities:
- Provide domain-level behavior (e.g., computing length).

Public API:
- `Message`: Immutable value object for message text.

Operational:
- Concurrency: Immutable; safe to share.
- Logging: None.

Usage
-----
from your_service_name.backend.models.message import Message
msg = Message(text="hi")
assert msg.length() == 2
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Message:
    """
    Value object for textual messages.

    Extended Summary
    ----------------
    Represents a domain-level message independent of HTTP or persistence.

    Attributes
    ----------
    text : str
        The message content.

    Examples
    --------
    >>> Message(text="abc").length()
    3
    """

    text: str

    def length(self) -> int:
        """
        Compute the character length of the message text.

        Returns
        -------
        int
            Number of characters in `text`.

        Examples
        --------
        >>> Message(text="hello").length()
        5
        """
        return len(self.text)
