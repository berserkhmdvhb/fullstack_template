# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Build throwaway Alembic script directories, and read or write a database's revision."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

REAL = Path(__file__).resolve().parents[2] / "src/your_service_name/backend/db/migrations"
FILE = """from __future__ import annotations

revision = "{rev}"
down_revision = {down}
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
"""


def make_script_dir(root: Path, revisions: list[tuple[str, str | None]]) -> Path:
    """A script dir: the real env.py and 0001 (the items table), then no-op revisions."""
    scripts = root / "migrations"
    (scripts / "versions").mkdir(parents=True)
    shutil.copy(REAL / "env.py", scripts / "env.py")
    for rev, down in revisions:
        if rev == "0001":
            shutil.copy(REAL / "versions/0001_create_items.py", scripts / "versions" / "0001.py")
            continue
        body = FILE.format(rev=rev, down=repr(down))
        (scripts / "versions" / f"{rev}.py").write_text(body, encoding="utf-8")
    return scripts


def db_file(url: str) -> str:
    """The file path of a ``sqlite+aiosqlite:///`` URL."""
    return url.split("///", 1)[1]


def stamp(url: str, revision: str | None) -> None:
    """Write ``revision`` into the database's ``alembic_version`` table (None: create it empty)."""
    with sqlite3.connect(db_file(url)) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)"
        )
        conn.execute("DELETE FROM alembic_version")
        if revision is not None:
            conn.execute("INSERT INTO alembic_version VALUES (?)", (revision,))


def revision_of(url: str) -> str | None:
    """The revision recorded in the database, or None when there is none."""
    with sqlite3.connect(db_file(url)) as conn:
        try:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        except sqlite3.OperationalError:
            return None
    return row[0] if row else None
