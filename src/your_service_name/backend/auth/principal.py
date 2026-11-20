# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Authenticated principal.

Responsibilities:
- Model the identity that a request acts on behalf of, as resolved by the auth
  dependency (`your_service_name.backend.auth.dependencies.get_current_user`).

This is deliberately a plain dataclass (not a Pydantic model): it is an internal
security object, not a request/response schema, so it should not participate in
serialization/validation.

Public API:
- `Principal`: the authenticated identity attached to a request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Principal:
    """
    The identity a request acts on behalf of.

    Attributes
    ----------
    user_id : str
        Stable identifier of the caller (gateway header value or JWT subject).
    identity_source : str
        How the identity was established: ``"gateway"``, ``"bearer"``, or
        ``"none"`` (auth disabled — see ``get_current_user``).
    roles : list[str]
        Optional authorization roles carried for the caller (gateway only).
    claims : dict[str, Any]
        Raw claims for token-based identity (bearer only); empty otherwise.
    """

    user_id: str
    identity_source: str
    roles: list[str] = field(default_factory=list)
    claims: dict[str, Any] = field(default_factory=dict)

    @property
    def is_anonymous(self) -> bool:
        """True when auth is disabled and no real identity was established."""
        return self.identity_source == "none"

    def has_role(self, role: str) -> bool:
        """
        Return True if the principal carries the given role.

        Parameters
        ----------
        role : str
            Role name to test for.
        """
        return role in self.roles
