# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
SAMPLE/REFERENCE: Async database module (SQLAlchemy 2.0).

Responsibilities:
- Provide async engine factory, session dependency, and lifespan hooks.
- Fall back to SQLite when DATABASE_URL is unset (local dev without PostgreSQL).

Public API:
- `DbSession`: Annotated AsyncSession dependency for endpoint injection.
- `get_session`: Generator yielding a session per request.
- `init_db`: Lifespan startup hook (creates the engine; never migrates).
- `close_db`: Lifespan shutdown hook (disposes engine).

Operational:
- Concurrency: AsyncSession per request; engine is shared.
- Logging: Logs engine creation and disposal.

Usage
-----
from your_service_name.backend.db import DbSession, init_db, close_db
"""

from __future__ import annotations

from your_service_name.backend.db.engine import (
    DbSession,
    close_db,
    get_session,
    init_db,
)

__all__ = ["DbSession", "close_db", "get_session", "init_db"]
