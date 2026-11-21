# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
The schema revisions this release expects, and how a database's revision compares.

Responsibilities:
- Read the bundled Alembic script directory (no database, no ``alembic.ini``).
- Classify a database revision as current, behind, ahead of this release, or missing.

Public API:
- `MIGRATIONS_DIR`: the bundled script directory.
- `load_revisions`: the head and every known revision; raises on multiple heads.
- `classify`: a database revision against those revisions.

Notes
-----
- A revision this release does not know is *ahead* (a newer release migrated first). That is
  ready, not broken: migrations stay compatible with the previous release (expand/contract).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from alembic.script import ScriptDirectory

MIGRATIONS_DIR: Path = Path(__file__).resolve().parent / "migrations"


class SchemaState(StrEnum):
    """Where a database stands against this release's migrations."""

    CURRENT = "current"
    BEHIND = "behind"
    AHEAD = "ahead"
    MISSING = "missing"


@dataclass(frozen=True)
class Revisions:
    """The head revision this code expects and every revision it knows."""

    head: str
    known: frozenset[str]


def load_revisions() -> Revisions:
    """Read the bundled script directory; more than one head is an error."""
    script = ScriptDirectory(str(MIGRATIONS_DIR))
    heads = script.get_heads()
    if len(heads) != 1:
        msg = f"multiple heads in the migration scripts ({len(heads)}); merge them"
        raise RuntimeError(msg)
    known = frozenset(rev.revision for rev in script.walk_revisions())
    return Revisions(head=heads[0], known=known)


def classify(current: str | None, revisions: Revisions) -> SchemaState:
    """Compare a database's revision (``None``: none recorded) with ``revisions``."""
    if not current:
        return SchemaState.MISSING
    if current == revisions.head:
        return SchemaState.CURRENT
    if current in revisions.known:
        return SchemaState.BEHIND
    return SchemaState.AHEAD
