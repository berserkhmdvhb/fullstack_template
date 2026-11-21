# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Connection arguments for PostgreSQL: the TLS context and the timeouts.

Responsibilities:
- Build the ``ssl`` argument from ``DB_SSLMODE`` / ``DB_SSLROOTCERT``.
- Build asyncpg ``connect_args`` for the app engine (timeouts and TLS).

Public API:
- `ssl_context`: ``False`` for ``disable``, else an ``ssl.SSLContext``.
- `app_connect_args`: asyncpg ``connect_args`` for the app engine.

Notes
-----
- ``require`` encrypts without verifying; ``verify-ca`` verifies the chain, not the hostname;
  ``verify-full`` verifies both. The verify modes trust ``DB_SSLROOTCERT`` alone when it is set,
  else the system trust store.
- Applies to PostgreSQL only; SQLite gets no connect arguments.
"""

from __future__ import annotations

import ssl
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from your_service_name.backend.settings import Settings


def ssl_context(settings: Settings) -> ssl.SSLContext | bool:
    """The asyncpg ``ssl`` argument for ``settings.DB_SSLMODE``; ``False`` turns TLS off."""
    mode = settings.DB_SSLMODE
    if mode == "disable":
        return False  # asyncpg's default would be "prefer", so this is passed explicitly
    if mode == "require":
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    else:
        context = ssl.create_default_context(cafile=settings.DB_SSLROOTCERT or None)
        context.check_hostname = mode == "verify-full"
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def app_connect_args(settings: Settings) -> dict[str, Any]:
    """asyncpg ``connect_args`` for the app engine: connect timeout, session timeouts, TLS."""
    return {
        "timeout": settings.DB_CONNECT_TIMEOUT_SECONDS,
        "ssl": ssl_context(settings),
        "server_settings": {
            "statement_timeout": str(settings.DB_STATEMENT_TIMEOUT_MS),
            "idle_in_transaction_session_timeout": str(settings.DB_IDLE_IN_TRANSACTION_TIMEOUT_MS),
        },
    }
