# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Single source for the SQLite fallback location.

The template's SQLite fallback (used when neither ``TEST_DATABASE_URL`` nor
``DATABASE_URL`` is set) used to hard-code ``data/items.db`` in three places
(``engine.py``, ``seed.py``) plus a fourth, divergent literal in ``alembic.ini``.
A concrete DB path duplicated across layers is exactly the "landmine for the next
adopter" Convention 3 forbids, so the relative path and the root-discovery rule
live here once and every consumer reads them.

The path is overridable with the ``SQLITE_PATH`` setting (relative to the project
root), so an adopter can retarget the fallback DB without touching code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from your_service_name.backend.settings import DEFAULT_SQLITE_REL_PATH

# Root is the first ancestor holding the project marker files; walking up (rather
# than counting ``..``) keeps the answer correct for modules at any package depth
# (db/engine.py vs scripts/seed.py) and for an installed package layout.
_ROOT_MARKERS: Final[tuple[str, ...]] = ("pyproject.toml", "alembic.ini")


def project_root(start: Path) -> Path:
    """Return the project root: the nearest ancestor of *start* holding a marker.

    An installed package (site-packages) has no marker above it, so fall back to the
    working directory's ancestors (the container WORKDIR holds ``alembic.ini``), and
    last to the working directory itself. The result is always a directory.
    """
    cur = start.resolve()
    cwd = Path.cwd().resolve()
    for parent in (cur, *cur.parents, cwd, *cwd.parents):
        if parent.is_dir() and any(parent.joinpath(marker).exists() for marker in _ROOT_MARKERS):
            return parent
    return cwd


def default_sqlite_path(root: Path, rel: str = DEFAULT_SQLITE_REL_PATH) -> Path:
    """Absolute path to the default SQLite DB (``root / rel``; *rel* is ``SQLITE_PATH``)."""
    return (root / rel).resolve()


def default_sqlite_url(root: Path, rel: str = DEFAULT_SQLITE_REL_PATH) -> str:
    """Async-compatible SQLAlchemy URL for the default SQLite DB."""
    return f"sqlite+aiosqlite:///{default_sqlite_path(root, rel)}"
