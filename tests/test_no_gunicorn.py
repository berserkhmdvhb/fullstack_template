# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The backend is served by Uvicorn only: no tracked file but the changelog names the old server."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = b"gun" + b"icorn"  # spelled in two parts so this file does not name it
HISTORY = {"wiki/changelog.md"}  # past entries stay as written
LOCKS = {"uv.lock", "package-lock.json"}


def _tracked() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"],  # noqa: S607 - git from PATH
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    return [p for p in out.stdout.decode().split("\0") if p]


def test_no_tracked_file_except_the_changelog_names_the_old_server() -> None:
    offenders = [
        path
        for path in _tracked()
        if path not in HISTORY
        and Path(path).name not in LOCKS
        and not path.endswith(".lock")
        and NAME in (ROOT / path).read_bytes().lower()
    ]
    assert not offenders, f"remove the old server from: {offenders}"
