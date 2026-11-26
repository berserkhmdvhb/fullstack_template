"""Copy the Next.js standalone server into a work dir, keeping pnpm's relative links."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def copy_standalone(bundle: Path, work: Path) -> None:
    """Copy ``bundle`` into ``work``; every symlink in the copy must resolve inside the copy.

    The links are copied as links: ``ignore_dangling_symlinks`` checks a relative link against the
    process's cwd, so it would drop every pnpm ``node_modules/<pkg> -> .pnpm/...`` link.
    """
    shutil.copytree(bundle, work, symlinks=True, dirs_exist_ok=True)
    root = work.resolve()
    # os.walk does not descend into linked dirs, so each link is checked once.
    for parent, dirs, files in os.walk(work):
        for link in (
            Path(parent, name) for name in dirs + files if Path(parent, name).is_symlink()
        ):
            target = link.resolve()
            inside = target.exists() and target.is_relative_to(root)
            assert inside, f"{link} -> {link.readlink()} does not resolve inside the copy"
